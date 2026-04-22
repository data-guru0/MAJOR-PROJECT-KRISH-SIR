import json
import operator
import re
from typing import TypedDict, Annotated

from langfuse.openai import OpenAI
from langgraph.graph import StateGraph, END
from langgraph.constants import Send

client = OpenAI()


def parse_json_response(raw: str) -> list:
    raw = raw.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if match:
        raw = match.group(1).strip()
    try:
        return json.loads(raw)
    except Exception:
        return []


class GraphState(TypedDict):
    diff: str
    patterns: list[str]
    findings: Annotated[list[dict], operator.add]


def static_analysis_node(state: GraphState) -> dict:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are a static analysis tool. Review this git diff for code complexity issues, unused variables, and poor naming. Return only a JSON array. Each item must have keys: file, line, severity (info/warning/error), message.",
            },
            {"role": "user", "content": state["diff"]},
        ],
    )
    raw = response.choices[0].message.content
    items = parse_json_response(raw)
    for item in items:
        item["agent"] = "static_analysis"
    return {"findings": items}


def security_node(state: GraphState) -> dict:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are a security scanner. Review this git diff for OWASP Top 10 vulnerabilities, hardcoded secrets, and SQL injection risks. Return only a JSON array. Each item must have keys: file, line, severity, message.",
            },
            {"role": "user", "content": state["diff"]},
        ],
    )
    raw = response.choices[0].message.content
    items = parse_json_response(raw)
    for item in items:
        item["agent"] = "security"
    return {"findings": items}


def style_node(state: GraphState) -> dict:
    patterns_str = "\n".join(state["patterns"]) if state["patterns"] else "None"
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": f"You are a code style reviewer. Review this git diff for formatting, readability, and consistency issues. Common patterns this team has had before: {patterns_str}. Return only a JSON array. Each item must have keys: file, line, severity, message.",
            },
            {"role": "user", "content": state["diff"]},
        ],
    )
    raw = response.choices[0].message.content
    items = parse_json_response(raw)
    for item in items:
        item["agent"] = "style"
    return {"findings": items}


def architecture_node(state: GraphState) -> dict:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are an architecture reviewer. Review this git diff for separation of concerns violations, missing error handling, and improper dependency usage. Return only a JSON array. Each item must have keys: file, line, severity, message.",
            },
            {"role": "user", "content": state["diff"]},
        ],
    )
    raw = response.choices[0].message.content
    items = parse_json_response(raw)
    for item in items:
        item["agent"] = "architecture"
    return {"findings": items}


def merge_node(state: GraphState) -> dict:
    seen = set()
    merged = []
    for finding in state["findings"]:
        key = (finding.get("file"), finding.get("line"))
        if key not in seen:
            seen.add(key)
            merged.append(finding)
    return {"findings": merged}


def fan_out(state: GraphState):
    return [
        Send("static_analysis", state),
        Send("security", state),
        Send("style", state),
        Send("architecture", state),
    ]


def build_graph() -> StateGraph:
    builder = StateGraph(GraphState)

    builder.add_node("static_analysis", static_analysis_node)
    builder.add_node("security", security_node)
    builder.add_node("style", style_node)
    builder.add_node("architecture", architecture_node)
    builder.add_node("merge", merge_node)

    builder.set_conditional_entry_point(fan_out)

    builder.add_edge("static_analysis", "merge")
    builder.add_edge("security", "merge")
    builder.add_edge("style", "merge")
    builder.add_edge("architecture", "merge")
    builder.add_edge("merge", END)

    return builder.compile()
