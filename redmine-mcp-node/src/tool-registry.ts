import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import {
  CallToolRequestSchema, ListToolsRequestSchema, type CallToolResult, type Tool, type ToolAnnotations,
} from '@modelcontextprotocol/sdk/types.js';
import { isObject, neutralizeUntrustedMarkers, type JsonObject } from './formatting.js';
import { TransportError } from './transport.js';
import { AdministratorsOwnedItemError } from './storage/store.js';

export const READ_ONLY = { readOnlyHint: true } satisfies ToolAnnotations;
export const WRITE = { readOnlyHint: false, destructiveHint: true, idempotentHint: false } satisfies ToolAnnotations;

export class ToolError extends Error {}

interface Field<T> {
  readonly schema: JsonObject;
  readonly read: (value: unknown, path: string) => T;
}

function invalid(path: string, expected: string): never {
  throw new ToolError(`${path} 必須是${expected}`);
}

export function string(description = ''): Field<string> {
  return { schema: { type: 'string', ...(description && { description }) }, read(value, path) {
    return typeof value === 'string' ? value : invalid(path, '字串');
  } };
}

export function integer(description = '', bounds: { minimum?: number; maximum?: number } = {}): Field<number> {
  return {
    schema: { type: 'integer', ...bounds, ...(description && { description }) },
    read(value, path) {
      // Pydantic accepts integral JSON numbers, numeric strings and booleans.
      const text = typeof value === 'string' ? value.trim() : '';
      const candidate = /^[-+]?\d+(?:\.0+)?$/.test(text) ? Number(text) : value;
      const number = typeof candidate === 'boolean' ? Number(candidate) : candidate;
      if (typeof number !== 'number' || !Number.isSafeInteger(number)) return invalid(path, '安全整數');
      if (bounds.minimum !== undefined && number < bounds.minimum) return invalid(path, `大於或等於 ${bounds.minimum} 的整數`);
      if (bounds.maximum !== undefined && number > bounds.maximum) return invalid(path, `小於或等於 ${bounds.maximum} 的整數`);
      return number;
    },
  };
}

export function boolean(description: string): Field<boolean> {
  return {
    schema: { type: 'boolean', description },
    read(value, path) {
      if (typeof value === 'boolean') return value;
      const enabled = typeof value === 'string' ? value.toLowerCase() : value;
      if (enabled === 1 || enabled === '1' || enabled === 'true' || enabled === 't' || enabled === 'on' || enabled === 'yes' || enabled === 'y') return true;
      if (enabled === 0 || enabled === '0' || enabled === 'false' || enabled === 'f' || enabled === 'off' || enabled === 'no' || enabled === 'n') return false;
      return invalid(path, '布林值');
    },
  };
}

export function nullable<T>(field: Field<T>): Field<T | null> {
  const { description, ...schema } = field.schema;
  return {
    schema: { anyOf: [schema, { type: 'null' }], default: null, description },
    read: (value, path) => value === null || value === undefined ? null : field.read(value, path),
  };
}

export function defaulted<T>(field: Field<T>, defaultValue: NoInfer<T>): Field<T> {
  return {
    schema: { ...field.schema, default: defaultValue },
    read: (value, path) => value === undefined ? defaultValue : field.read(value, path),
  };
}

export function list<T>(item: Field<T>, description = ''): Field<T[]> {
  return {
    schema: { items: item.schema, type: 'array', ...(description && { description }) },
    read(value, path) {
      if (!Array.isArray(value)) return invalid(path, '陣列');
      return value.map((entry, index) => item.read(entry, `${path}[${index}]`));
    },
  };
}

export function dictionary(description: string): Field<JsonObject>;
export function dictionary<T>(description: string, item: Field<T>): Field<Record<string, T>>;
export function dictionary(description: string, item?: Field<unknown>): Field<JsonObject> {
  return {
    schema: { additionalProperties: item?.schema ?? true, type: 'object', ...(description && { description }) },
    read(value, path) {
      if (!isObject(value)) return invalid(path, '物件');
      return item ? Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, item.read(entry, path)])) : value;
    },
  };
}

export const readSite = nullable(string('站台代號；省略則查詢所有已設定的站台。可用 list_sites 取得站台清單。'));
export const writeSite = nullable(string('要寫入的站台代號；設定多個站台時必填。可用 list_sites 取得站台清單。'));

type Fields = Record<string, Field<unknown>>;
type Arguments<F extends Fields> = { [K in keyof F]: ReturnType<F[K]['read']> };

export interface ToolRegistry {
  register<F extends Fields>(
    definition: { name: string; description: string; annotations: ToolAnnotations; fields: F },
    handler: (args: Arguments<F>, signal: AbortSignal) => JsonObject | Promise<JsonObject>,
  ): void;
}

function preParseValue(value: unknown, field: Field<unknown>): unknown {
  // The Python MCP SDK accepts arrays and objects that desktop clients sent as JSON strings.
  if (typeof value !== 'string' || field.schema.type === 'string') return value;
  const trimmed = value.trim();
  if (trimmed === 'null') return null;
  if (trimmed.startsWith('[') || trimmed.startsWith('{')) {
    try {
      return JSON.parse(trimmed);
    } catch {
      // Keep the original text for field validation.
    }
  }
  return value;
}

export function bindToolRegistry(mcp: McpServer, redact: (text: string) => string): ToolRegistry {
  const tools = new Map<string, {
    definition: Tool;
    run: (args: JsonObject, signal: AbortSignal) => JsonObject | Promise<JsonObject>;
  }>();
  // Use the SDK request handlers so registration can advertise the existing JSON Schema
  // exactly, including optional fields, open objects and nullable defaults.
  mcp.server.setRequestHandler(ListToolsRequestSchema, () => ({
    tools: Array.from(tools.values(), ({ definition }) => definition),
  }));
  mcp.server.setRequestHandler(CallToolRequestSchema, async ({ params }, { signal }): Promise<CallToolResult> => {
    const tool = tools.get(params.name);
    const name = neutralizeUntrustedMarkers(params.name);
    const failure = (text: string): CallToolResult => ({
      isError: true, content: [{ type: 'text', text: redact(text) }],
    });
    if (!tool) return failure(`Unknown tool: ${name}`);
    try {
      const result = await tool.run(params.arguments ?? {}, signal);
      return { structuredContent: result, content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    } catch (error) {
      if (error instanceof AdministratorsOwnedItemError) {
        return failure(`Error executing tool ${name}: ${AdministratorsOwnedItemError.diagnostic}`);
      }
      const message = error instanceof ToolError || error instanceof TransportError
        ? error.message
        : '執行 Redmine 工具時發生非預期錯誤';
      return failure(`Error executing tool ${name}: ${message}`);
    }
  });
  return {
    register({ name, description, annotations, fields }, handler) {
      if (tools.has(name)) throw new Error(`Duplicate tool: ${name}`);
      const entries = Object.entries(fields);
      const required = entries.filter(([, { schema }]) => !Object.hasOwn(schema, 'default')).map(([key]) => key);
      tools.set(name, {
        definition: {
          name, description, annotations,
          inputSchema: {
            type: 'object',
            properties: Object.fromEntries(entries.map(([key, field]) => [key, field.schema])),
            ...(required.length && { required }),
          },
          outputSchema: { type: 'object', additionalProperties: true },
        },
        run(args, signal) {
          const parsed = Object.fromEntries(entries.map(([key, field]) => [
            key, field.read(preParseValue(Object.hasOwn(args, key) ? args[key] : undefined, field), key),
          ])) as Arguments<typeof fields>;
          return handler(parsed, signal);
        },
      });
    },
  };
}
