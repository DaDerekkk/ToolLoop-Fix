"""Standard-library Qwen XML-tool protocol adapter; never invents model actions."""
import copy
import json
import re

ADAPTER_VERSION = "qwen_xml_single_call_v1"
SYSTEM_PROMPT = (
    "You are completing a task in a simulated tool environment. Use the provided tools. "
    "Return exactly one tool call per turn; do not call tools in parallel. "
    "After obtaining the requested result, call finish_task with the exact result value "
    "and identifier from the tool response. If you cannot proceed, call stop_task with "
    "the reason code from the observed response. Never invent a result or identifier."
)
FINAL_TOOL = "finish_task"
STOP_TOOL = "stop_task"


def tool_schemas(view):
    schemas = []
    for name, doc in view["tools"].items():
        properties = {key: {"type": "string"} for key in doc["arguments"]}
        if name == "ask_user":
            properties["field"]["enum"] = ["identifier"]
        schemas.append({"type": "function", "function": {"name": name,
            "description": doc["description"], "parameters": {"type": "object",
            "properties": properties, "required": list(properties), "additionalProperties": False}}})
    for name, fields, description in (
        (FINAL_TOOL, ("answer", "identifier"), "Finish with the exact requested value and identifier already returned by a tool."),
        (STOP_TOOL, ("reason",), "Stop without claiming success; give the observed reason code."),
    ):
        schemas.append({"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": {field: {"type": "string"} for field in fields},
                           "required": list(fields), "additionalProperties": False}}})
    return schemas


def parse_tool_output(raw_text, token_limit_reached=False):
    """Parse official Qwen3 coder-style XML, accepting only one call per turn.

    Plain commentary before a single call is retained, not executed. Malformed,
    multiple, truncated or terminal-wrapper-invalid outputs become one invalid
    protocol attempt; no first-call salvage, inferred arguments or fallback.
    """
    result = {"valid": False, "error": None, "call": None, "action": None,
              "assistant_content": raw_text, "requested_tool_call_count": raw_text.count("<tool_call>")}
    if token_limit_reached:
        result["error"] = "OUTPUT_TOKEN_LIMIT"
        return result
    text = raw_text.rstrip()
    for marker in ("<|im_end|>", "<|endoftext|>"):
        if text.endswith(marker):
            text = text[:-len(marker)].rstrip()
    if "<|im_start|>" in text or "<tool_response>" in text:
        result["error"] = "UNEXPECTED_CONVERSATION_MARKER"
        return result
    if text.count("<tool_call>") != 1 or text.count("</tool_call>") != 1:
        result["error"] = "EXPECTED_EXACTLY_ONE_TOOL_CALL"
        return result
    match = re.search(r"<tool_call>\s*<function=([A-Za-z_][A-Za-z0-9_]*)>(.*?)</function>\s*</tool_call>", text, re.S)
    if not match:
        result["error"] = "MALFORMED_TOOL_XML"
        return result
    if text[match.end():].strip():
        result["error"] = "UNEXPECTED_TOOL_CALL_SUFFIX"
        return result
    name, body = match.group(1), match.group(2)
    arguments, cursor = {}, 0
    for parameter in re.finditer(r"<parameter=([A-Za-z_][A-Za-z0-9_]*)>(.*?)</parameter>", body, re.S):
        if body[cursor:parameter.start()].strip() or parameter.group(1) in arguments:
            result["error"] = "INVALID_OR_DUPLICATE_PARAMETER"
            return result
        value = parameter.group(2).strip()
        if "<parameter=" in value or "<function=" in value:
            result["error"] = "NESTED_TOOL_XML"
            return result
        arguments[parameter.group(1)] = value
        cursor = parameter.end()
    if body[cursor:].strip():
        result["error"] = "UNPARSED_PARAMETER_CONTENT"
        return result
    if name == FINAL_TOOL:
        if set(arguments) != {"answer", "identifier"}:
            result["error"] = "INVALID_FINISH_ARGUMENTS"
            return result
        action = {"type": "final", **arguments}
    elif name == STOP_TOOL:
        if set(arguments) != {"reason"}:
            result["error"] = "INVALID_STOP_ARGUMENTS"
            return result
        action = {"type": "stop", **arguments}
    else:
        action = {"type": "tool", "tool": name, "arguments": arguments}
    result.update(valid=True, call={"name": name, "arguments": arguments}, action=action,
                  assistant_content=text[:match.start()].strip())
    return result


def messages_for_view(view, decisions):
    """Build history from public view only; decisions preserve exact model output.

    A parse failure has no executable call, so its feedback is an explicit user
    protocol-error message, not a fabricated tool call/result pair.
    """
    if len(view["history"]) != len(decisions):
        raise ValueError("each public event needs exactly one recorded model decision")
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "Complete this request: " + json.dumps(view["request"], ensure_ascii=False)}]
    for index, (event, decision) in enumerate(zip(view["history"], decisions)):
        if decision["valid"]:
            call_id = "call_{}".format(index + 1)
            call = decision["call"]
            messages.append({"role": "assistant", "content": decision["assistant_content"],
                "tool_calls": [{"id": call_id, "type": "function", "function": copy.deepcopy(call)}]})
            messages.append({"role": "tool", "name": call["name"], "tool_call_id": call_id,
                             "content": json.dumps(event["response"], ensure_ascii=False)})
        else:
            messages.append({"role": "assistant", "content": decision["raw_text"]})
            messages.append({"role": "user", "content": "Adapter protocol error; no tool was executed. " +
                             json.dumps({"parser_error": decision["error"], "response": event["response"]}, ensure_ascii=False)})
    return messages
