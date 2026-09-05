import os
from loguru import logger
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

class CodePatch(BaseModel):
    filepath: str = Field(description="The exact relative filepath of the file to modify.")
    target_function: str = Field(description="The exact name of the function or class to replace.")
    explanation: str = Field(description="The reasoning for this specific fix. Must be generated before the code.")
    fixed_code: str = Field(description="The raw, complete python or javascript code for the fixed function. Do NOT include markdown fences like ```python.")

class Coder:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY is missing from .env file. The Coder will fail.")
            
        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-2.5-flash" 
        
    def generate_patch(self, bug_report: str, old_code: str) -> str | None:
        def generate_patch(self, bug_report: str, xml_context: str) -> MultiFilePatch | None:
        """
        Takes a bug report and a multi-file XML context document.
        Returns a MultiFilePatch containing a list of edits, or None if the API fails.
        """
        logger.info(f"Generating patch for bug: '{bug_report[:30]}...'")
        
        system_prompt = """
        You are a senior software engineer.
        You will be provided with a bug report and an XML document containing relevant code chunks from the repository.
        Your job is to analyze the context, determine which files and functions need to change, and output the exact rewritten functions.
        
        CRITICAL RULES:
        - Return ONLY the raw code for the new functions.
        - Do NOT include any conversational text outside the explanation field.
        - Keep the exact same function name and parameters unless the bug explicitly requires changing them.
        """
        
        user_prompt = f"""
        # Bug Report
        {bug_report}
        
        # Codebase Context (XML)
        {xml_context}
        """
        
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
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