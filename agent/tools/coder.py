import os
from loguru import logger
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from agent.core.prompts import SYSTEM_PROMPT, first_attempt_prompt, retry_prompt

load_dotenv()

class CodePatch(BaseModel):
    filepath: str = Field(description="The exact relative filepath of the file to modify.")
    target_function: str = Field(description="The exact name of the function or class to replace.")
    explanation: str = Field(description="The reasoning for this specific fix. Must be generated before the code.")
    fixed_code: str = Field(description="The raw, complete python or javascript code for the fixed function. Do NOT include markdown fences like ```python.")

class MultiFilePatch(BaseModel):
    edits: list[CodePatch] = Field(description="The list of edits to apply (one per changed function).")

class Coder:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY is missing from .env file. The Coder will fail.")
            
        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-2.5-flash" 
        
    def generate_patch(
        self,
        bug_report: str,
        xml_context: str,
        previous_patch=None,
        failure_output: str | None = None,
    ) -> MultiFilePatch | None:
        """
        First attempt uses the plain prompt. A retry passes previous_patch +
        failure_output so the model gets its rejected edit and the verbatim test output.
        Returns a MultiFilePatch containing a list of edits, or None if the API fails.
        """
        logger.info(f"Generating patch for bug: '{bug_report[:30]}...'")

        if previous_patch is None and failure_output is None:
            user_prompt = first_attempt_prompt(bug_report, xml_context)
        else:
            user_prompt = retry_prompt(bug_report, xml_context, previous_patch, failure_output or "(no output captured)")
        
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=MultiFilePatch,
                ),
            )
            
            patch_data = response.parsed
            
            if not patch_data or not patch_data.edits:
                logger.error("Gemini failed to return structured edits.")
                return None
                
            logger.success(f"Successfully generated {len(patch_data.edits)} code edits via Gemini.")
            return patch_data
            
        except Exception as e:
            logger.error(f"Gemini API call failed: {e}")
            return None