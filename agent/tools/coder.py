import os
from loguru import logger
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class CodePatch(BaseModel):
    """
    Our strict Pydantic contract. Gemini will be mathematically forced 
    to return a JSON object matching this exact structure.
    """
    fixed_code: str = Field(description="The raw, complete python or javascript code for the fixed function. Do NOT include markdown fences like ```python.")

class Coder:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY is missing from .env file. The Coder will fail.")
            
        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-2.5-flash" 
        
    def generate_patch(self, bug_report: str, old_code: str) -> str | None:
        """
        Takes a bug report and a broken code chunk.
        Returns the raw, fixed code string, or None if the API fails.
        """
        logger.info(f"Generating patch for bug: '{bug_report[:30]}...'")
        
        system_prompt = """
        You are an elite, senior software engineer.
        You will be provided with a bug report and a specific function.
        Your job is to rewrite the function to fix the bug.
        
        CRITICAL RULES:
        - Return ONLY the raw code for the new function.
        - Do NOT include any explanations or conversational text.
        - Keep the exact same function name and parameters.
        """
        
        user_prompt = f"""
        # Bug Report
        {bug_report}
        
        # Target Function Code
        {old_code}
        """
        
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=CodePatch,
                ),
            )
            
            patch_data = response.parsed
            
            if not patch_data or not patch_data.fixed_code:
                logger.error("Gemini failed to return structured code.")
                return None
                
            logger.success("Successfully generated code patch via Gemini.")
            return patch_data.fixed_code
            
        except Exception as e:
            logger.error(f"Gemini API call failed: {e}")
            return None