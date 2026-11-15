import os
from datetime import date

import anthropic
from anthropic import beta_tool
from fastapi import HTTPException

import fetching
import series

MODEL = "claude-opus-5-5"
DAILY_CHATS = 300
SYSTEM = (
    "You answer questions about how Earth-science variables have changed in Romania, inside the Terra app.\n"
    "All you know about the data is what the trends tool returns. Call it once with the id of the variable the question is about, chosen from the list below. "
    "When several fit, take the most direct one, for example power:T2M for warming and power:PRECTOTCORR for rain.\n"
    "Without a place the tool returns the change at the seats of Romania's 42 counties over each place's whole record, and the page shows it on the map. "
    "When the question names one Romanian county or town, pass it as place, and pass the period it names as start and end. "
    "The tool then returns that place's own series, and the page fills in the place, period and variable and opens the chart. "
    "A period needs a place: when the question gives a period and no place, say that the map covers the whole record.\n"
    "Then answer in at most three short bullet points that start with \"• \", under 80 words in all, in plain words: "
    "where it changes fastest, or how it changed at the named place, by how much, and how sure the change is. Use only numbers the tool returned. "
    "Do not mention ids, tools or p-values, and write no markup such as bold: the page shows your text as it is.\n"
    "If no listed variable fits, or the question is not about change in Romania, say in one sentence what you can answer.\n\n"
    "Variables (id | name | theme | unit | kind):\n"
    + "\n".join(f"{v['id']} | {v['name']} | {v['theme']} | {v['unit'] or 'no unit'} | {v['kind']}" for v in series.VARIABLES.values())
)
TOOL = {"type": "object", "additionalProperties": False, "required": ["variable"], "properties": {
    "variable": {"type": "string", "description": "A variable id from the list in the system prompt, for example power:T2M."},
    "place": {"type": "string", "description": "The one Romanian county or town the question names, name only, for example Alba or Turda. Leave out when it names none."},
    "kind": {"type": "string", "enum": ["county", "town"], "description": "Set only when the question says whether the place is a county or a town."},
    "start": {"type": "string", "description": "First month of the period the question names, as YYYY-MM. A year starts in its January. Leave out when it names no start."},
    "end": {"type": "string", "description": "Last month of that period, as YYYY-MM. A year ends in its December. Leave out when the period runs until now."}}}
_spent = {"day": None, "count": 0}


def change(slope, p, years):
    if p >= 0.05:
        return f"no clear change in {years} years"
    total = slope * (years - 1) / 10
    evidence = "very strong" if p < 0.001 else "strong" if p < 0.01 else "moderate"
    return f"{'rose' if total > 0 else 'fell'} by {abs(total):.3g} in {years} years, {abs(slope):.3g} per decade, {evidence} evidence"


def describe(variable):
    rows = series.TRENDS.get(variable)
    if not rows:
        return "No such variable. Use an id from the list."
    clear = sum(row["p_value"] < 0.05 for row in rows)
    lines = [f"{series.VARIABLES[variable]['name']}, unit {series.VARIABLES[variable]['unit'] or 'none'}. {clear} of {len(rows)} county seats show a clear change and {len(rows) - clear} show none. Fastest change first:"]
    for row in rows:
        lines.append(f"{row['name']} ({row['county']}): {change(row['slope_per_decade'], row['p_value'], row['years'])}")
    return "\n".join(lines)


def at(variable, place, start, end):
    status, data = fetching.answer(variable, None, place["id"], start, end)
    label = f"{place['name']} county, the box around its towns" if place["kind"] == "county" else f"{place['name']} in {place['county']} county"
    if status != 200:
        return f"The series at {label} is still being fetched from NASA. The page shows the chart as soon as it arrives. Say that, without numbers."
    values, trend, about = [point["value"] for point in data["points"] if point["value"] is not None], data["trend"], data["variable"]
    if not values:
        return f"No values at {label} in that period. The record runs from {about['first_month']} to {about['last_month']}."
    return (f"{about['name']}, unit {about['unit'] or 'none'}, at {label}, {data['period']['start']} to {data['period']['end']}: "
            f"{len(values)} monthly values, lowest {min(values):.3g}, highest {max(values):.3g}, mean {sum(values) / len(values):.3g}. "
            + (f"Trend: {change(trend['slope_per_decade'], trend['p_value'], trend['years'])}." if trend else f"No trend: {data['trend_reason']}"))


def ask(question):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "The chat needs a key. Set ANTHROPIC_API_KEY on the server.")
    if _spent["day"] != date.today():
        _spent.update(day=date.today(), count=0)
    if _spent["count"] >= DAILY_CHATS:
        raise HTTPException(429, "Today's chat budget is used up. The map and the time series still work.")
    _spent["count"] += 1
    picked = {}

    @beta_tool(name="trends", input_schema=TOOL, description="How one variable changed. Without a place: at the seats of Romania's 42 counties over each place's whole record, "
               "with total change, rate per decade and strength of evidence for each. With a place: that place's own series over the period.")
    def trends(variable: str, place: str = "", kind: str = "", start: str = "", end: str = "") -> str:
        found = sorted(series.find_places(place), key=lambda match: match["kind"] != kind)[:1]
        if place and not found:
            return f"No Romanian county or town matches {place}. Use another name, or leave the place out."
        try:
            text = at(variable, found[0], start or None, end or None) if found else describe(variable)
        except HTTPException as error:
            return error.detail
        if found or variable in series.TRENDS:
            picked.update(variable=variable, place=found[0] if found else None, start=start, end=end)
        return text

    try:
        message = anthropic.Anthropic(timeout=60).beta.messages.tool_runner(
            model=MODEL, max_tokens=4000, max_iterations=4, tools=[trends], output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}, {"type": "text", "text": f"Today is {date.today()}."}],
            messages=[{"role": "user", "content": question}]).until_done()
    except anthropic.AuthenticationError:
        raise HTTPException(503, "The chat key was refused. Check ANTHROPIC_API_KEY.") from None
    except anthropic.RateLimitError:
        raise HTTPException(429, "The chat is busy. Try again in a minute.") from None
    except anthropic.APIStatusError as error:
        raise HTTPException(502, f"The chat service returned HTTP {error.status_code}.") from None
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Cannot reach the chat service. Try again shortly.") from None
    answer = "" if message.stop_reason == "refusal" else "\n".join(block.text for block in message.content if block.type == "text").strip()
    return {"answer": answer or "I cannot answer that. Ask where a variable is changing in Romania, for example: where is it warming fastest?",
            "unit": series.VARIABLES[picked["variable"]]["unit"] if picked else "", **picked}
