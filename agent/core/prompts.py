SYSTEM_PROMPT = """
You are a senior software engineer.
You will be provided with a bug report and an XML document containing relevant code chunks from the repository.
Your job is to analyze the context, determine which files and functions need to change, and output the exact rewritten functions.

CRITICAL RULES:
- Return ONLY the raw code for the new functions.
- Do NOT include any conversational text outside the explanation field.
- Keep the exact same function name and parameters unless the bug explicitly requires changing them.
"""


def first_attempt_prompt(bug_report: str, xml_context: str) -> str:
    """Builds the user message for the initial patch request."""
    return f"""
# Bug Report
{bug_report}

# Codebase Context (XML)
{xml_context}
"""


def render_previous_patch(previous_patch) -> str:
    """Renders an applied MultiFilePatch as readable text for retry feedback."""
    sections = []
    for edit in previous_patch.edits:
        sections.append(
            f"File: {edit.filepath}\n"
            f"Function replaced: {edit.target_function}\n"
            f"Code that was applied:\n{edit.fixed_code}"
        )
    return "\n\n".join(sections)


def retry_prompt(
    bug_report: str,
    xml_context: str,
    previous_patch,
    failure_output: str,
) -> str:
    """
    Builds the user message for a retry: original mission, what the previous
    patch did, and the verbatim tail of the failed test run.
    """
    return f"""
# Bug Report
{bug_report}

# Codebase Context (XML)
{xml_context}

# Previous Attempt (REJECTED BY THE TEST SUITE)
{render_previous_patch(previous_patch)}

# Test Failure Output (verbatim, produced by the attempt above)
{failure_output}

REQUIREMENTS:
- Your previous edit did not fix the bug. Do NOT return a functionally identical edit.
- Read the failure output carefully and address the exact error it reports.
- Adjust your reasoning in the explanation field before writing new code.
"""
