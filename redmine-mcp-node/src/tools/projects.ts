import { nameText, neutralizeUntrustedMarkers, object, pythonRepr } from '../formatting.js';
import { requestObject, type SiteRegistry } from '../sites.js';
import { contracts } from '../tool-contracts.js';
import { ToolError, type ToolRegistry } from '../tool-registry.js';

export function registerProjects(tools: ToolRegistry, sites: SiteRegistry): void {
  tools.register(contracts.create_project, async ({ site, name, identifier, ...options }, signal) => {
    const [siteName, target] = sites.resolveForWrite(site);
    const trimmedName = name.trim();
    if (!trimmedName) throw new ToolError('name 不可為空白');
    const trimmedIdentifier = identifier.trim();
    if (!/^[a-z0-9][a-z0-9_-]{0,99}$/.test(trimmedIdentifier)) {
      throw new ToolError('identifier 只能用小寫英文、數字、減號與底線，需以英數開頭且長度 1–100，' +
        `不接受中文或大寫（收到：${neutralizeUntrustedMarkers(pythonRepr(identifier))}）`);
    }
    const project = {
      name: trimmedName, identifier: trimmedIdentifier,
      ...Object.fromEntries(Object.entries(options).filter(([, value]) => value !== null)),
    };
    const raw = await requestObject(target, 'POST', '/projects.json', { signal, body: { project } });
    const created = object(raw.project);
    return {
      site: siteName, id: created.id ?? null, name: nameText(created.name), identifier: created.identifier || project.identifier,
    };
  });
}
