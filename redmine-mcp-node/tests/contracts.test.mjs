import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// Tool names registered by the four Python modules at 747bd4a.
const registrations = {
  metadata: [
    "get_current_user",
    "list_custom_fields",
    "list_issue_statuses",
    "list_priorities",
    "list_projects",
    "list_sites",
    "list_trackers",
  ],
  issues: ["add_issue_note", "create_issue", "get_issue", "list_issues", "update_issue"],
  attachments: ["download_attachment", "list_attachments", "upload_attachment"],
  projects: ["create_project"],
};

// Keep the fixture as captured from tools/list. A cross-SDK comparison may
// ignore member order, generated schema titles and a dialect marker that does
// not change the schema's interpretation. Treat omitted required as [] and
// omitted additionalProperties as true. Compare equivalent type-array and
// anyOf forms only when all branches contain a single type and no other keywords.
// Descriptions, annotations, defaults, required names and constraints still
// need to match, including the difference between nullable and optional fields.
test("v0.17.4 tool contract is available", async () => {
  const fixtureUrl = new URL("./fixtures/v0.17.4-tools.json", import.meta.url);
  const { tools } = JSON.parse(await readFile(fixtureUrl, "utf8"));
  assert.deepEqual(
    tools.map((tool) => tool.name).sort(),
    Object.values(registrations).flat().sort(),
  );
  for (const tool of tools) {
    assert.ok(tool.description?.trim(), tool.name);
    assert.equal(tool.inputSchema?.type, "object", tool.name);
    assert.equal(tool.outputSchema?.type, "object", tool.name);
    assert.ok(tool.annotations, tool.name);
  }
});
