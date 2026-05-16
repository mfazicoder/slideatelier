/**
 * Tool contract shared by every tool implementation.
 *
 * `inputSchema` is a JSON Schema object (not a Zod schema directly) because
 * the MCP protocol exchanges raw JSON Schema with the client. We use Zod
 * inside each tool's handler for parsing and validation, then `.toJSON()`-
 * style export the schema via zod-to-json-schema when registering.
 *
 * For tools with no inputs we still pass `{ type: "object", properties: {} }`
 * — MCP clients reject tools without a schema.
 */

export interface Tool {
  /** Stable tool identifier the AI calls. */
  name: string;
  /** One-line summary used by the AI to decide whether to call. */
  description: string;
  /** JSON Schema for the tool's input object. */
  inputSchema: Record<string, unknown>;
  /** Handler invoked with the validated input. Throws on error. */
  handler: (input: unknown) => Promise<ToolResult>;
}

export interface ToolResult {
  /** Free-form text result the AI gets verbatim. */
  text: string;
  /** Optional structured payload — useful when the AI needs to act on the result. */
  data?: unknown;
}

/** Convenience: build a JSON Schema object that accepts no inputs. */
export const EMPTY_SCHEMA: Record<string, unknown> = {
  type: "object",
  properties: {},
  additionalProperties: false,
};
