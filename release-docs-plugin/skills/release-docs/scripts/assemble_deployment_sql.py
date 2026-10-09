"""Assemble authoritative ordered SQL units without rewriting their text.

Static checks are deliberately conservative, not a SQL parser or proof that a
deployment is idempotent. Opaque procedure execution, batch splits and transaction
ownership inside units require repaired authoritative source before assembly.
No SQL is read from the repository or executed against a database here.
"""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re

PHASES = ("SCHEMA", "REPAIR", "DATA", "VALIDATION")
Finding = dict
_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}\Z")
_HEX = re.compile(r"[0-9a-fA-F]{64}\Z")
_ASCII_UPPER = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_CONTEXT = ("ReleaseId", "ValidateOnly", "Phase", "UnitId", "SourcePath",
            "SourceRevision", "SourceLine", "TransactionAction", "ErrorNumber",
            "ErrorSeverity", "ErrorState", "ErrorProcedure", "ErrorLine",
            "ErrorMessage", "ErrorXactState", "ErrorTranCount")


@dataclass
class ArtifactRecord:
    path: Path
    sha256: str
    release_id: str
    transaction_mode: int
    unit_mapping: list
    findings: list = field(default_factory=list)


class SQLContractError(ValueError):
    """Blocking findings contain metadata, never SQL or credentials."""

    def __init__(self, findings):
        self.findings = findings
        super().__init__("SQL contract blocked: " + ", ".join(sorted({f["code"] for f in findings})))


def _finding(code, **metadata):
    return {"code": code, "blocking": True, **metadata}


def _lex(sql):
    """Mask comments, literals and quoted identifiers, preserving offsets/lines.

    Nested block comments and doubled quote delimiters follow SQL Server lexical
    rules. Unterminated constructs block rather than hiding the rest of a file.
    """
    chars = list(sql)
    index = 0
    malformed = False

    def mask(start, end):
        for offset in range(start, end):
            if chars[offset] not in "\r\n":
                chars[offset] = " "

    while index < len(sql):
        start = index
        if sql.startswith("--", index):
            while index < len(sql) and sql[index] not in "\r\n":
                index += 1
            mask(start, index)
        elif sql.startswith("/*", index):
            index += 2
            depth = 1
            while index < len(sql) and depth:
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            malformed |= bool(depth)
            mask(start, index)
        elif sql[index] in "'\"[":
            close = "]" if sql[index] == "[" else sql[index]
            index += 1
            closed = False
            while index < len(sql):
                if sql[index] == close:
                    index += 1
                    if index < len(sql) and sql[index] == close:
                        index += 1
                    else:
                        closed = True
                        break
                else:
                    index += 1
            malformed |= not closed
            mask(start, index)
        else:
            index += 1
    # Unicode uppercasing can expand characters (e.g. ß -> SS), which would
    # invalidate the source offsets used for literal/error-context checks.
    return "".join(chars).translate(_ASCII_UPPER), malformed


def _matches(pattern, code):
    return re.search(pattern, code, re.MULTILINE | re.DOTALL) is not None


def _hazards(sql, is_unit=False):
    code, malformed = _lex(sql)
    findings = []
    checks = [
        ("batch_separator", r"^\s*GO\b[^\r\n]*$"),
        ("non_transactional_sql", r"\b(?:ALTER|CREATE|DROP)\s+DATABASE\b|\b(?:BACKUP|RESTORE|RECONFIGURE)\b|\bWITH\s+ROLLBACK\s+IMMEDIATE\b|\bDBCC\s+SHRINK(?:DATABASE|FILE)\b|\b(?:CREATE|ALTER|DROP)\s+FULLTEXT\b|\bALTER\s+INDEX\b[^;]*\bREORGANIZE\b"),
        ("batch_only_sql", r"\b(?:CREATE(?:\s+OR\s+ALTER)?|ALTER)\s+(?:PROC(?:EDURE)?|VIEW|FUNCTION|TRIGGER)\b"),
        ("xact_abort_disabled", r"\bSET\s+XACT_ABORT\s+OFF\b"),
        ("early_exit", r"\b(?:RETURN|GOTO)\b"),
        ("database_switch", r"\bUSE\b"),
        ("opaque_execution", r"\bEXEC(?:UTE)?\b|^\s*:[A-Z]+\b"),
    ]
    if is_unit:
        checks.extend([
            ("unit_transaction_control", r"\b(?:BEGIN|SAVE)\s+TRAN(?:SACTION)?\b|\b(?:COMMIT|ROLLBACK)\b"),
            ("reserved_context_mutation", r"@(?:" + "|".join(_CONTEXT).upper() + r")\b"),
        ])
    for name, pattern in checks:
        if _matches(pattern, code):
            findings.append(_finding(name))
    if malformed:
        findings.append(_finding("malformed_sql_lexeme"))
    catches = list(re.finditer(r"\bBEGIN\s+CATCH\b(.*?)\bEND\s+CATCH\b", code, re.S))
    if len(catches) != len(re.findall(r"\bBEGIN\s+CATCH\b", code)):
        findings.append(_finding("silent_catch"))
    for catch in catches:
        # Require a final standalone rethrow, rather than IF ... THROW or a
        # handler that prints/logs and resumes execution. Semicolon boundaries
        # keep the wrapper's SELECT CASE ... ELSE ... END from looking like an
        # IF/ELSE statement governing the final THROW.
        if not re.search(r"(?:^|;)\s*THROW\s*;\s*$", catch.group(1)):
            findings.append(_finding("silent_catch"))
    for raise_match in re.finditer(r"\bRAISERROR\b", code):
        end = code.find(";", raise_match.end())
        if end < 0 or not re.match(r"\s*THROW\b", code[end + 1:]):
            findings.append(_finding("raiserror_without_throw"))
    return findings


def validate_sql_contract(sql_text) -> list[Finding]:
    """Validate conservative lexical invariants of one release-level wrapper.

    Findings are blocking. Passing means the static contract is present, not that
    the SQL was parsed, executed, provider-validated or proven safe to rerun.
    """
    code, _ = _lex(sql_text)
    findings = _hazards(sql_text)
    if not _matches(r"\bSET\s+XACT_ABORT\s+ON\s*;", code):
        findings.append(_finding("missing_xact_abort"))
    tries = list(re.finditer(r"\bBEGIN\s+TRY\b(.*?)\bEND\s+TRY\s+BEGIN\s+CATCH\b(.*?)\bEND\s+CATCH\b", code, re.S))
    if not tries:
        findings.append(_finding("missing_try_catch"))
    if len(re.findall(r"\bBEGIN\s+TRAN(?:SACTION)?\b", code)) != 1 or len(re.findall(r"\bCOMMIT\b", code)) != 1 or len(re.findall(r"\bROLLBACK\b", code)) != 2 or _matches(r"\bSAVE\s+TRAN", code):
        findings.append(_finding("transaction_count"))
    rollback = r"\bIF\s+@VALIDATEONLY\s*=\s*1\s+BEGIN\s+ROLLBACK\s+TRAN(?:SACTION)?\s*;"
    commit = r"\bELSE\s+IF\s+@VALIDATEONLY\s*=\s*0\s+BEGIN\s+COMMIT\s+TRAN(?:SACTION)?\s*;"
    try_code = tries[-1].group(1) if tries else ""
    if not _matches(rollback, try_code):
        findings.append(_finding("validate_only_rollback"))
    if not _matches(commit, try_code):
        findings.append(_finding("deploy_commit"))
    if not _matches(r"\bTHROW\s*;", code):
        findings.append(_finding("missing_throw"))
    if tries:
        catch = tries[-1].group(2)
        if (len(re.findall(r"\bBEGIN\s+TRAN(?:SACTION)?\b", try_code)) != 1
                or len(re.findall(r"\bCOMMIT\b", try_code)) != 1
                or len(re.findall(r"\bROLLBACK\b", try_code)) != 1
                or len(re.findall(r"\bROLLBACK\b", catch)) != 1):
            findings.append(_finding("transaction_scope"))
        if not _matches(r"\bIF\s+XACT_STATE\s*\(\s*\)\s*<>\s*0\s+BEGIN\s+ROLLBACK\s+TRAN(?:SACTION)?\s*;", catch):
            findings.append(_finding("error_rollback"))
        fields = ("ReleaseId", "DatabaseName", "ServerName", "ValidateOnly", "Phase", "UnitId", "SourcePath", "SourceRevision", "SourceLine", "ErrorNumber", "ErrorSeverity", "ErrorState", "ErrorProcedure", "ErrorLine", "ErrorMessage", "XactState", "TranCount", "TransactionAction")
        required = [r"\bAS\s+" + field.upper() + r"\b" for field in fields]
        required.extend(r"\b" + func + r"\s*\(\s*\)" for func in ("ERROR_NUMBER", "ERROR_SEVERITY", "ERROR_STATE", "ERROR_PROCEDURE", "ERROR_LINE", "ERROR_MESSAGE", "XACT_STATE", "DB_NAME"))
        required.extend((r"@@TRANCOUNT\b", r"\bSERVERPROPERTY\s*\("))
        if any(not _matches(pattern, catch) for pattern in required):
            findings.append(_finding("missing_error_context"))
        original_catch = sql_text[tries[-1].start(2):tries[-1].end(2)]
        actions = list(re.finditer(r"\bSET\s+@TRANSACTIONACTION\s*=\s*", catch))
        if not any(re.match(r"N?'ROLLBACK'\s*;", original_catch[m.end():], re.I) for m in actions):
            findings.append(_finding("missing_transaction_action"))
    else:
        findings.append(_finding("missing_error_context"))
    return findings


def _mode(transaction_mode, units):
    mode = transaction_mode
    release = None
    if isinstance(mode, dict):
        release = mode.get("release_id")
        mode = mode.get("validate_only")
    if type(mode) is not int or mode not in (0, 1):
        raise ValueError("transaction_mode requires validate_only 0 or 1")
    if release is None:
        provenance = [{k: u[k] for k in ("unit_id", "source_path", "source_revision", "source_hash", "sql_hash")} for u in units]
        release = "release-" + hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()[:24]
    if not isinstance(release, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", release):
        raise ValueError("Invalid release identifier")
    return mode, release


def _validate_units(units):
    seen = set()
    last_phase = -1
    findings = []
    for unit in units:
        if not isinstance(unit, dict):
            raise ValueError("Invalid unit descriptor")
        try:
            uid, phase, sql = unit["unit_id"], unit["phase"], unit["sql"]
            source, revision, source_hash = unit["source_path"], unit["source_revision"], unit["source_hash"]
            dependencies = unit["depends_on"]
            valid = (isinstance(uid, str) and bool(_ID.fullmatch(uid)) and uid not in seen
                     and phase in PHASES and PHASES.index(phase) >= last_phase
                     and unit["complete"] is True and isinstance(sql, str) and bool(sql.strip())
                     and isinstance(source, str) and 0 < len(source) <= 1024
                     and not any(ord(c) < 32 for c in source)
                     and not re.search(r"(?:password|token|secret)\s*[=:]", source, re.I)
                     and isinstance(revision, str) and bool(re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", revision))
                     and isinstance(source_hash, str) and bool(_HEX.fullmatch(source_hash))
                     and type(unit["source_line"]) is int and unit["source_line"] >= 1
                     and isinstance(dependencies, list) and all(isinstance(d, str) and d in seen for d in dependencies)
                     and unit["sql_hash"] == hashlib.sha256(sql.encode("utf-8")).hexdigest())
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError("Incomplete, inconsistent or unordered unit descriptor")
        seen.add(uid)
        last_phase = PHASES.index(phase)
        for finding in _hazards(sql, is_unit=True):
            findings.append({**finding, "unit_id": uid, "source_path": source, "source_line": unit["source_line"]})
    if findings:
        raise SQLContractError(findings)


def _literal(text):
    return "N'" + text.replace("'", "''") + "'"


def assemble_deployment_sql(units, output_path, transaction_mode) -> ArtifactRecord:
    """Write the fixed operator SQL filename after all contract checks pass.

    transaction_mode is 0/1, or {validate_only: 0/1, release_id: safe identifier}.
    Ordered descriptors are consumed exactly as provided; unsafe source must be
    repaired upstream. Mapping records precise original and artifact line ranges.
    """
    units = list(units)
    _validate_units(units)
    mode, release = _mode(transaction_mode, units)
    output = Path(output_path)
    if output.name != "01_部署SQL.sql":
        raise ValueError("Output must be named 01_部署SQL.sql")
    template = (Path(__file__).parents[1] / "assets/sql_transaction_wrapper.sql").read_text(encoding="utf-8")
    template = template.replace("{{RELEASE_ID}}", _literal(release)).replace("{{VALIDATE_ONLY}}", str(mode))
    prefix, suffix = template.split("{{UNITS}}")
    body = ""
    mappings = []
    for phase in PHASES:
        body += "\n-- PHASE: " + phase + "\n    SET @Phase = " + _literal(phase) + ";\n"
        for unit in (u for u in units if u["phase"] == phase):
            mapping = {key: unit[key] for key in ("unit_id", "phase", "source_path", "source_revision", "source_hash", "sql_hash", "source_line", "depends_on")}
            source_lines = len(unit["sql"].splitlines())
            mapping["source_end_line"] = unit["source_line"] + source_lines - 1
            body += "-- UNIT: " + json.dumps(mapping, ensure_ascii=True, sort_keys=True) + "\n"
            for variable, key in (("UnitId", "unit_id"), ("SourcePath", "source_path"), ("SourceRevision", "source_revision")):
                body += "    SET @" + variable + " = " + _literal(unit[key]) + ";\n"
            body += "    SET @SourceLine = " + str(unit["source_line"]) + ";\n"
            mapping["artifact_start_line"] = len((prefix + body).splitlines()) + 1
            mapping["artifact_end_line"] = mapping["artifact_start_line"] + source_lines - 1
            body += unit["sql"]
            if not unit["sql"].endswith("\n"):
                body += "\n"
            body += "-- END UNIT: " + unit["unit_id"] + "\n"
            mappings.append(mapping)
    sql = prefix + body + suffix
    findings = validate_sql_contract(sql)
    if findings:
        raise SQLContractError(findings)
    data = sql.encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    return ArtifactRecord(output, hashlib.sha256(data).hexdigest(), release, mode, mappings)
