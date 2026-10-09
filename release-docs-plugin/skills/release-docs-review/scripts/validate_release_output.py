"""Review operator inventory against lifecycle truth; never execute SQL.

Returns findings followed by SQL-content and deployment-validation status
records. Static passing does not establish provider compatibility or runtime
rollback, commit, convergence or data preservation. LocalDB evidence is separate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from review_fingerprint import (OPERATOR_FILES, execution_path, file_hash,
                                lifecycle_root, plain_path, read_json)

# Load the assembler's public static validator, including when invoked directly.
sys.path.insert(0, str(Path(__file__).parents[2] / "release-docs/scripts"))
from assemble_deployment_sql import PHASES, _lex, validate_sql_contract

Finding = dict
STATUSES = ("通過", "待確認", "未通過")
REPORT_CODES = frozenset("""
undeclared_output missing_operator_file unsafe_review_path missing_lifecycle_evidence
invalid_lifecycle_exclusions missing_parameter_document unexpected_parameter_document
parameter_applicability_unknown conflicting_parameter_applicability unreadable_operator_file
missing_execution_artifact_evidence execution_artifact_mismatch unsafe_or_missing_execution_artifact
invalid_unit_evidence blocking_source_analysis excluded_dependency unresolved_dependency
missing_or_unordered_phases excluded_object_in_sql invalid_unit_mapping excluded_unit_in_sql
unexpected_or_duplicate_unit unit_dependency_order unit_mapping_mismatch unit_phase_mismatch
incomplete_unit_mapping missing_unit_context unit_context_mismatch unit_sql_hash_mismatch
rerun_risk included_unit_mapping_mismatch duplicate_column guide_exclusion_mismatch
incomplete_deployment_evidence unsupported_deployment_pass_claim deployment_proved_sql_defect
semantic_review_pending semantic_sql_defect invalid_finding
batch_separator non_transactional_sql batch_only_sql early_exit database_switch opaque_execution
unit_transaction_control reserved_context_mutation implicit_transactions_enabled xact_abort_disabled
execution_disabled malformed_sql_lexeme silent_catch raiserror_without_throw missing_xact_abort
missing_try_catch transaction_count missing_implicit_transactions_off unconditional_transaction_start
validate_only_rollback deploy_commit missing_throw transaction_scope error_rollback missing_error_context
missing_transaction_action unmapped_sql invalid_exclusion_provenance
""".split())


def _finding(code, blocking=True, domain="sql_content", status=None):
    return {"code": code, "blocking": blocking, "domain": domain,
            "status": status or ("未通過" if blocking else "待確認")}


def _summary(findings, deployment="待確認"):
    sql = [f for f in findings if f["domain"] == "sql_content"]
    status = "未通過" if any(f["blocking"] for f in sql) else "待確認" if sql else "通過"
    return findings + [_finding("sql_content_status", status == "未通過", status=status),
                       _finding("deployment_validation_status", deployment == "未通過",
                                domain="deployment_validation", status=deployment)]


def _deployment(metadata, digest):
    validation = metadata.get("localdb_validation")
    if not isinstance(validation, dict):
        return "待確認"
    if validation.get("status") == "failed":
        return "未通過"
    if validation.get("status") != "passed" or validation.get("artifact_sha256") != digest:
        return "待確認"
    rounds = validation.get("rounds")
    if not isinstance(rounds, dict):
        return "待確認"
    text_fields = ("server", "database", "provider_version", "tool_version", "baseline_source", "fixture_source")
    for name in ("validate_only", "commit", "rerun", "injected_failure"):
        evidence = rounds.get(name)
        if (not isinstance(evidence, dict) or evidence.get("status") != "passed"
                or type(evidence.get("exit_code")) is not int or evidence["exit_code"] != 0
                or any(not isinstance(evidence.get(k), str) or not evidence[k].strip() for k in text_fields)
                or not isinstance(evidence.get("command"), (str, list)) or not evidence["command"]
                or not isinstance(evidence.get("checks"), (list, dict)) or not evidence["checks"]
                or not isinstance(evidence.get("error_output_summary"), str)
                or (name == "injected_failure" and not evidence["error_output_summary"].strip())):
            return "待確認"
    return "通過"


def _visible_sql(text):
    """Preserve quoted identifiers while masking comments and string values."""
    code, _ = _lex(text)
    chars = list(code)
    # Only restore bracket names when the bracket occurs in code, not a string
    # or comment. A small scanner determines lexical context independently.
    pattern = r'''--[^\r\n]*|/\*|N?'(?:''|[^'])*'|\[(?:\]\]|[^\]])*\]|"(?:""|[^"])*"'''
    index = 0
    while index < len(text):
        match = re.search(pattern, text[index:], re.I)
        if match is None:
            break
        start, end = index + match.start(), index + match.end()
        token = text[start:end]
        if token == "/*":
            depth, end = 1, end
            while end < len(text) and depth:
                if text.startswith("/*", end):
                    depth, end = depth + 1, end + 2
                elif text.startswith("*/", end):
                    depth, end = depth - 1, end + 2
                else:
                    end += 1
        elif token.startswith(("[", '"')):
            restored = " " + token[1:-1].replace("]]", "]").replace('""', '"') + " "
            # Identifiers are limited by unit metadata to simple ASCII names.
            if len(restored) == end - start:
                chars[start:end] = restored.upper()
        index = end
    return "".join(chars)


def _projection(exclusions):
    # Same public operator fields as Task 4, independently checked here.
    from render_release_documents import _text
    lines = []
    for item in exclusions:
        lines.append("- 排除名稱：" + "、".join(_text(v) for v in item["issues"]) +
                     "；原因：" + _text(item["reason"]) +
                     "；影響範圍：" + "、".join(_text(v) for v in item["objects"]) +
                     "；正式環境保留與操作：" + _text(item["operator_action"]) +
                     "；重新納入前置條件：" + "、".join(_text(v) for v in item["reinstatement_conditions"]))
    return "\n".join(lines) if lines else "Lifecycle 證據確認本次沒有排除範圍。"


def _rerun_risk(body):
    code, _ = _lex(body)
    guards = (
        (r"\bCREATE\s+TABLE\b", r"\bIF\s+(?:OBJECT_ID\s*\([^;]*?\)\s+IS\s+NULL|NOT\s+EXISTS\s*\([^;]*?\bSYS\s*\.\s*TABLES\b)"),
        (r"\bCREATE\s+(?:UNIQUE\s+)?(?:CLUSTERED\s+|NONCLUSTERED\s+)?INDEX\b", r"\bIF\s+NOT\s+EXISTS\s*\([^;]*?\bSYS\s*\.\s*INDEXES\b"),
        (r"\bALTER\s+TABLE\b[^;]*\bADD\b", r"\bIF\s+(?:COL_LENGTH\s*\([^;]*?\)\s+IS\s+NULL|NOT\s+EXISTS\s*\([^;]*?\bSYS\s*\.\s*(?:COLUMNS|FOREIGN_KEYS|KEY_CONSTRAINTS|CHECK_CONSTRAINTS|DEFAULT_CONSTRAINTS)\b)"),
        (r"\bINSERT\s+(?:INTO\s+)?", r"\bIF\s+NOT\s+EXISTS\s*\("),
    )
    return any(re.search(mutation, code) and not re.search(guard, code) for mutation, guard in guards)


def _unmapped_sql(sql):
    """Require the assembler frame and account for every executable body span."""
    first = sql.find("\n-- PHASE: SCHEMA")
    last = sql.rfind("\n    SET @Phase = N'FINALIZE';")
    if first < 0 or last < first:
        return True
    header = re.search(r"DECLARE @ReleaseId nvarchar\(128\) = (N'[A-Za-z0-9_.:-]{1,128}');", sql)
    mode = re.search(r"DECLARE @ValidateOnly bit = ([01]);", sql)
    if header is None or mode is None:
        return True
    template = (Path(__file__).parents[2] / "release-docs/assets/sql_transaction_wrapper.sql").read_text(encoding="utf-8")
    prefix, suffix = template.replace("{{RELEASE_ID}}", header[1]).replace("{{VALIDATE_ONLY}}", mode[1]).split("{{UNITS}}")
    if sql[:first].rstrip() != prefix.rstrip() or sql[last:].lstrip() != suffix.lstrip():
        return True
    region = sql[first:last]
    region = re.sub(r"^-- UNIT: [^\r\n]+\r?\n.*?^-- END UNIT: [^\r\n]+(?:\r?\n|$)", "", region, flags=re.M | re.S)
    region = re.sub(r"^-- PHASE: (SCHEMA|REPAIR|DATA|VALIDATION)\r?\n    SET @Phase = N'\1';", "", region, flags=re.M)
    code, malformed = _lex(region)
    return malformed or bool(code.strip())


def _exclusion_provenance(source_units, evidence, exclusions):
    """Resolve full Task 2 exclusions against authoritative lifecycle records."""
    if not exclusions:
        return []
    by_id = {u["unit_id"]: u for u in source_units}
    sources = evidence.get("sources")
    if len(by_id) != len(source_units) or not isinstance(sources, list):
        return [_finding("invalid_exclusion_provenance")]
    unit_fields = ("unit_id", "phase", "complete", "depends_on", "issues", "objects", "source_path",
                   "source_revision", "source_hash", "source_line", "sql_hash", "covered_sources")
    provenance_fields = ("unit_id", "source_path", "source_revision", "source_hash")
    all_excluded = set()
    for exclusion in exclusions:
        ids = set(exclusion["unit_ids"])
        records, artifacts = exclusion.get("units"), exclusion.get("evidence")
        impact = exclusion.get("dependency_impact")
        valid = (ids <= set(by_id) and not ids & all_excluded
                 and isinstance(records, list) and len(records) == len(ids)
                 and isinstance(artifacts, list) and len(artifacts) == len(ids)
                 and isinstance(impact, list) and all(isinstance(uid, str) for uid in impact)
                 and all(isinstance(exclusion.get(k), str) and exclusion[k].strip()
                         for k in ("exclusion_id", "authorization_status", "review_status")))
        if not valid:
            return [_finding("invalid_exclusion_provenance")]
        all_excluded.update(ids)
        for collection in (records, artifacts):
            if (any(not isinstance(item, dict) or not isinstance(item.get("unit_id"), str) for item in collection)
                    or {item["unit_id"] for item in collection} != ids):
                return [_finding("invalid_exclusion_provenance")]
        for record in records:
            unit = by_id[record["unit_id"]]
            if (any(key not in unit or key not in record or record[key] != unit[key] for key in unit_fields)
                    or not isinstance(unit["source_path"], str) or not unit["source_path"]
                    or not isinstance(unit["source_revision"], str)
                    or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", unit["source_revision"])
                    or any(not isinstance(unit[k], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", unit[k])
                           for k in ("source_hash", "sql_hash"))
                    or any(not isinstance(unit[k], list) or any(not isinstance(v, str) for v in unit[k])
                           for k in ("objects", "issues", "covered_sources"))):
                return [_finding("invalid_exclusion_provenance")]
            if not any(isinstance(item, dict) and all(item.get(k) == unit[k] for k in provenance_fields[1:])
                       for item in sources):
                return [_finding("invalid_exclusion_provenance")]
        if any(any(item.get(k) != by_id[item["unit_id"]].get(k) for k in provenance_fields) for item in artifacts):
            return [_finding("invalid_exclusion_provenance")]
        if (set(exclusion["objects"]) != {obj for uid in ids for obj in by_id[uid]["objects"]}
                or set(exclusion["issues"]) != {issue for uid in ids for issue in by_id[uid]["issues"]}):
            return [_finding("invalid_exclusion_provenance")]
        affected = set(ids)
        while True:
            expanded = affected | {u["unit_id"] for u in source_units if set(u["depends_on"]) & affected}
            if expanded == affected:
                break
            affected = expanded
        if set(impact) != affected - ids:
            return [_finding("invalid_exclusion_provenance")]
    return []


def _units(sql, evidence, exclusions):
    findings = []
    source_findings = evidence.get("findings")
    if (not isinstance(source_findings, list)
            or any(not isinstance(f, dict) or not isinstance(f.get("code"), str) or not f["code"].strip()
                   or type(f.get("blocking")) is not bool for f in source_findings)):
        return [_finding("invalid_unit_evidence")]
    source_units = evidence.get("units")
    if not isinstance(source_units, list) or any(not isinstance(u, dict) for u in source_units):
        return [_finding("invalid_unit_evidence")]
    for unit in source_units:
        if (unit.get("complete") is not True or unit.get("phase") not in PHASES
                or not isinstance(unit.get("unit_id"), str)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]*", unit["unit_id"])
                or not isinstance(unit.get("depends_on"), list)
                or any(not isinstance(d, str) for d in unit["depends_on"])
                or type(unit.get("source_line")) is not int or unit["source_line"] < 1):
            return [_finding("invalid_unit_evidence")]
    excluded = {uid for item in exclusions for uid in item["unit_ids"]}
    findings.extend(_exclusion_provenance(source_units, evidence, exclusions))
    expected = {u.get("unit_id"): u for u in source_units if u.get("unit_id") not in excluded}
    if len(expected) != len([u for u in source_units if u.get("unit_id") not in excluded]) or None in expected:
        findings.append(_finding("invalid_unit_evidence"))
    if any(f["blocking"] for f in source_findings):
        findings.append(_finding("blocking_source_analysis"))
    for unit in expected.values():
        dependencies = unit.get("depends_on")
        if not isinstance(dependencies, list):
            findings.append(_finding("invalid_unit_evidence"))
        elif set(dependencies) & excluded:
            findings.append(_finding("excluded_dependency"))
        elif set(dependencies) - set(expected):
            findings.append(_finding("unresolved_dependency"))
    phase_positions = [sql.find("-- PHASE: " + phase) for phase in PHASES]
    if any(p < 0 for p in phase_positions) or phase_positions != sorted(phase_positions):
        findings.append(_finding("missing_or_unordered_phases"))
    seen = set()
    code = _visible_sql(sql)
    for item in exclusions:
        for object_name in item["objects"]:
            parts = object_name.replace("[", "").replace("]", "").split(".")
            pattern = r"(?<![A-Z0-9_])" + r"\s*\.\s*".join(re.escape(p.upper()) for p in parts) + r"(?![A-Z0-9_])"
            if re.search(pattern, code):
                findings.append(_finding("excluded_object_in_sql"))
    matches = list(re.finditer(r"^-- UNIT: ([^\r\n]+)\r?$", sql, re.M))
    for match in matches:
        try:
            mapping = json.loads(match.group(1))
            uid = mapping["unit_id"]
            if (not isinstance(uid, str) or not isinstance(mapping.get("depends_on"), list)
                    or any(not isinstance(d, str) for d in mapping["depends_on"])):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            findings.append(_finding("invalid_unit_mapping"))
            continue
        if uid in excluded:
            findings.append(_finding("excluded_unit_in_sql"))
        if uid in seen or uid not in expected:
            findings.append(_finding("unexpected_or_duplicate_unit"))
        if isinstance(mapping.get("depends_on"), list) and any(d not in seen for d in mapping["depends_on"]):
            findings.append(_finding("unit_dependency_order"))
        seen.add(uid)
        unit = expected.get(uid)
        if unit is None:
            continue
        fields = ("unit_id", "phase", "source_path", "source_revision", "source_hash", "sql_hash", "source_line", "depends_on")
        if any(mapping.get(k) != unit.get(k) for k in fields):
            findings.append(_finding("unit_mapping_mismatch"))
        preceding = [phase for phase, position in zip(PHASES, phase_positions) if 0 <= position < match.start()]
        if not preceding or preceding[-1] != unit["phase"]:
            findings.append(_finding("unit_phase_mismatch"))
        end = re.search(r"^-- END UNIT: " + re.escape(uid) + r"\r?$", sql[match.end():], re.M)
        if end is None:
            findings.append(_finding("incomplete_unit_mapping"))
            continue
        body = sql[match.end():match.end() + end.start()].lstrip("\r\n")
        # Four source context assignments precede the exact source SQL bytes.
        assignments = (("UnitId", "unit_id"), ("SourcePath", "source_path"), ("SourceRevision", "source_revision"), ("SourceLine", "source_line"))
        for name, key in assignments:
            context = re.match(r"\s*SET @" + name + r" = [^\r\n]+\r?\n", body)
            if context is None:
                findings.append(_finding("missing_unit_context"))
                break
            value = unit.get(key)
            literal = str(value) if name == "SourceLine" else "N'" + str(value).replace("'", "''") + "'"
            if context.group(0).strip() != "SET @" + name + " = " + literal + ";":
                findings.append(_finding("unit_context_mismatch"))
            body = body[context.end():]
        candidates = (body, body[:-1] if body.endswith("\n") else body,
                      body.replace("\n", "\r\n"), body.replace("\n", "\r\n").removesuffix("\r\n"))
        if not any(hashlib.sha256(value.encode()).hexdigest() == unit.get("sql_hash") for value in candidates):
            findings.append(_finding("unit_sql_hash_mismatch"))
        if _rerun_risk(body):
            findings.append(_finding("rerun_risk"))
    if seen != set(expected):
        findings.append(_finding("included_unit_mapping_mismatch"))
    additions = re.findall(r"\bALTER\s+TABLE\s+([A-Z_][A-Z0-9_]*(?:\s*\.\s*[A-Z_][A-Z0-9_]*)*)\s+ADD\s+([A-Z_][A-Z0-9_]*)", code)
    additions = [(re.sub(r"\s+", "", table), column) for table, column in additions if column != "CONSTRAINT"]
    if len(additions) != len(set(additions)):
        findings.append(_finding("duplicate_column"))
    return findings


def validate_release_output(output_dir, run_root) -> list[Finding]:
    findings = []
    try:
        output, run = plain_path(output_dir), lifecycle_root(run_root)
        if ".release-docs" in [p.casefold() for p in output.parts]:
            raise ValueError("Operator output cannot be lifecycle")
        if output.exists():
            for path in output.iterdir():
                plain_path(path)
                if path.name not in OPERATOR_FILES or not path.is_file():
                    findings.append(_finding("undeclared_output"))
        for name in OPERATOR_FILES[:2]:
            if not plain_path(output / name).is_file():
                findings.append(_finding("missing_operator_file"))
    except (ValueError, OSError):
        return _summary([_finding("unsafe_review_path")])
    documents = {}
    for name in ("lifecycle_exclusion_manifest.json", "source_unit_metadata.json", "lifecycle_metadata.json"):
        try:
            plain_path(run / name)
        except ValueError:
            findings.append(_finding("unsafe_review_path"))
            continue
        try:
            documents[name] = read_json(run / name)
        except ValueError:
            findings.append(_finding("missing_lifecycle_evidence", blocking=False))
    if len(documents) != 3:
        return _summary(findings)
    metadata = documents["lifecycle_metadata.json"]
    source = documents["source_unit_metadata.json"]
    exclusions = documents["lifecycle_exclusion_manifest.json"].get("exclusions")
    valid = isinstance(exclusions, list)
    if valid:
        for item in exclusions:
            if (not isinstance(item, dict)
                    or any(not isinstance(item.get(k), list) or any(not isinstance(v, str) for v in item[k])
                           for k in ("issues", "unit_ids", "objects", "reinstatement_conditions"))
                    or any(not isinstance(item.get(k), str) or not item[k].strip() for k in ("reason", "operator_action"))):
                valid = False
                break
            if (not item["unit_ids"] or not item["issues"] or not item["objects"] or not item["reinstatement_conditions"]
                    or len(item["unit_ids"]) != len(set(item["unit_ids"]))
                    or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]*", uid) for uid in item["unit_ids"])
                    or any(not re.fullmatch(r"#[0-9]+", issue) for issue in item["issues"])):
                valid = False
                break
    if not valid:
        return _summary(findings + [_finding("invalid_lifecycle_exclusions")])
    contract = metadata.get("operator_contract", {})
    applicable = contract.get("parameters_applicable") if isinstance(contract, dict) else None
    if type(applicable) is bool and isinstance(source.get("parameter_changes"), list) and applicable != bool(source["parameter_changes"]):
        findings.append(_finding("conflicting_parameter_applicability"))
    if applicable is None and isinstance(source.get("parameter_changes"), list):
        applicable = bool(source["parameter_changes"])
    present = (output / OPERATOR_FILES[2]).is_file()
    if applicable is True and not present:
        findings.append(_finding("missing_parameter_document"))
    elif applicable is False and present:
        findings.append(_finding("unexpected_parameter_document"))
    elif type(applicable) is not bool and present:
        findings.append(_finding("parameter_applicability_unknown", blocking=False))
    sql_path, guide_path = output / OPERATOR_FILES[1], output / OPERATOR_FILES[0]
    if not sql_path.is_file() or not guide_path.is_file():
        return _summary(findings)
    try:
        sql = sql_path.read_text(encoding="utf-8-sig")
        guide = guide_path.read_text(encoding="utf-8-sig")
    except (UnicodeError, OSError):
        return _summary(findings + [_finding("unreadable_operator_file")])
    digest = file_hash(sql_path)
    try:
        artifact = execution_path(run, metadata)
        if artifact is None:
            findings.append(_finding("missing_execution_artifact_evidence", blocking=False))
        elif file_hash(artifact) != digest or metadata["execution_artifact"].get("sha256") != digest:
            findings.append(_finding("execution_artifact_mismatch"))
    except (ValueError, OSError):
        findings.append(_finding("unsafe_or_missing_execution_artifact"))
    findings.extend(_finding(f["code"]) for f in validate_sql_contract(sql))
    if _unmapped_sql(sql):
        findings.append(_finding("unmapped_sql"))
    findings.extend(_units(sql, source, exclusions))
    section = re.search(r"^## 本次排除摘要\s*\n(.*?)(?=^## |\Z)", guide, re.M | re.S)
    if section is None or section.group(1).strip() != _projection(exclusions):
        findings.append(_finding("guide_exclusion_mismatch"))
    deployment = _deployment(metadata, digest)
    validation = metadata.get("localdb_validation")
    if isinstance(validation, dict) and validation.get("status") == "passed" and deployment != "通過":
        findings.append(_finding("incomplete_deployment_evidence", blocking=False, domain="deployment_validation"))
    if re.search(r"LocalDB\s*[：:]\s*通過", guide) and deployment != "通過":
        findings.append(_finding("unsupported_deployment_pass_claim", blocking=True, domain="deployment_validation"))
    if isinstance(validation, dict) and validation.get("status") == "failed" and validation.get("sql_defect") is True:
        findings.append(_finding("deployment_proved_sql_defect"))
    # Deduplicate codes while keeping stable order and separate status records.
    findings = list({(f["domain"], f["code"]): f for f in findings}.values())
    return _summary(findings, deployment)


def _report_status(findings, domain, summary_code):
    """Never upgrade any prior summary or hide a later semantic finding."""
    rank = {value: index for index, value in enumerate(STATUSES)}
    summaries = [f.get("status") if f.get("status") in STATUSES else "待確認"
                 for f in findings if isinstance(f, dict) and f.get("code") == summary_code]
    status = max(summaries or ["待確認"], key=rank.get)
    for finding in findings:
        if not isinstance(finding, dict) or finding.get("code") in ("sql_content_status", "deployment_validation_status"):
            continue
        code = finding.get("code")
        finding_domain = "sql_content" if code in ("semantic_sql_defect", "semantic_review_pending") else finding.get("domain", "sql_content")
        if finding_domain != domain:
            continue
        candidate = "未通過" if finding.get("blocking") is True or code == "semantic_sql_defect" else finding.get("status", "待確認")
        if candidate not in STATUSES or code == "semantic_review_pending" and candidate == "通過":
            candidate = "待確認"
        status = max((status, candidate), key=rank.get)
    return status


def write_review_report(run_root, findings, fingerprint):
    """Write review evidence only in lifecycle; include codes, never source prose."""
    run = lifecycle_root(run_root)
    report = plain_path(run / "05_版更審查報告.md")
    values = {"SQLSTATUS": _report_status(findings, "sql_content", "sql_content_status"),
              "DEPLOYSTATUS": _report_status(findings, "deployment_validation", "deployment_validation_status"),
              "FINGERPRINT": fingerprint.sha256, "FINDINGS": ""}
    for key in ("SQLSTATUS", "DEPLOYSTATUS"):
        if values[key] not in STATUSES:
            values[key] = "待確認"
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint.sha256):
        raise ValueError("Invalid fingerprint record")
    lines = []
    for finding in findings:
        code = finding.get("code") if isinstance(finding, dict) else None
        if code not in ("sql_content_status", "deployment_validation_status"):
            safe = code if isinstance(code, str) and code in REPORT_CODES else "invalid_finding"
            lines.append("- " + safe)
    values["FINDINGS"] = "\n".join(lines) or "無靜態阻擋項目；部署驗證依獨立證據判定。"
    template = (Path(__file__).parents[1] / "assets/05_版更審查報告.md").read_text(encoding="utf-8")
    report.write_text(re.sub(r"\{\{([A-Z]+)\}\}", lambda m: values[m[1]], template), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-root", required=True)
    args = parser.parse_args()
    result = validate_release_output(args.output_dir, args.run_root)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    raise SystemExit(1 if any(f["blocking"] for f in result) else 0)
