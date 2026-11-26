/** Select an available read-only consistency skill using the BDD closeout context. */
export function select_skill(context) {
  if (!context.needsCodeConsistency) return { status: 'UNAVAILABLE', skill: null };
  let highest = 0;
  let selected = null;
  for (const candidate of context.availableSkills ?? []) {
    const name = String(candidate.name ?? '');
    const text = (name + ' ' + (candidate.description ?? '')).toLowerCase();
    let score = /consistency|一致/.test(text) ? 40 : 0;
    if (text.includes('issue') && /git|程式|code/.test(text)) score += 30;
    if (String(context.issueSystem ?? '').toLowerCase() === 'redmine' && name === 'issue-code-consistency-check') score += 100;
    if (score > highest || (score && score === highest && name > selected)) {
      highest = score;
      selected = name;
    }
  }
  return selected === null ? { status: 'UNAVAILABLE', skill: null } : { status: 'SELECTED', skill: selected };
}

/** Require complete BDD, screenshot and consistency evidence before a Redmine update. */
export function can_update_redmine(evidence) {
  const bdd = evidence.bdd ?? {};
  const screenshots = evidence.screenshots ?? {};
  return evidence.consistency === 'PASS' &&
    ['failed', 'blocked', 'notRun'].every((key) => !Object.hasOwn(bdd, key) || bdd[key] === 0 || bdd[key] === false) &&
    screenshots.allMasked === true && screenshots.attachmentsVerified === true;
}
