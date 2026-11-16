import { pathToFileURL } from 'node:url';

try {
  console.warn = () => {};
  const { loadSettings } = await import(pathToFileURL(process.argv[2]).href);
  await loadSettings(process.env);
} catch {
  console.error('Service configuration is invalid.');
  process.exitCode = 1;
}
