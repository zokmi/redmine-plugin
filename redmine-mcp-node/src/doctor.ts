import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

export async function runDoctor(): Promise<void> {
  if (process.platform !== 'win32') throw new Error('DOCTOR_FAILED: The local Redmine MCP service doctor requires Windows.');
  const { LOCALAPPDATA, SystemRoot } = process.env;
  const installed = LOCALAPPDATA && join(LOCALAPPDATA, 'redmine-mcp', 'service', 'doctor', 'doctor.ps1');
  const bundled = fileURLToPath(new URL('../../scripts/doctor.ps1', import.meta.url));
  const helper = installed && existsSync(installed) ? installed : bundled;
  if (!SystemRoot || !existsSync(helper)) throw new Error('DOCTOR_FAILED: The doctor helper is unavailable. Run the current service installer or use scripts/doctor.ps1 from the repository.');

  const powershell = join(SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  const child = spawn(powershell, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', helper], {
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'ignore'],
  });
  const message = new Promise<string>(resolve => {
    let output = '';
    child.stdout.setEncoding('utf8').on('data', (chunk: string) => {
      output += chunk;
      if (output.length > 4096) {
        resolve('');
        child.stdout.destroy();
      } else if (output.includes('\n')) {
        resolve(output.trim());
      }
    });
    child.stdout.once('end', () => resolve(output.trim()));
    child.stdout.once('error', () => resolve(''));
  });
  try {
    // A relaunched Windows process can inherit the stdout pipe past the doctor's exit.
    const [code] = await once(child, 'exit');
    const status = await Promise.race([message, delay(1000, '', {ref:false})]);
    if ((code !== 0 && code !== 1) || !/^[A-Z_]+: [^\r\n]+$/.test(status)) throw new Error();
    console.log(status);
    process.exitCode = code;
  } catch {
    throw new Error('DOCTOR_FAILED: The PowerShell doctor helper did not return a diagnostic status.');
  } finally {
    child.stdout.destroy();
  }
}
