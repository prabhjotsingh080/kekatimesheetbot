import os
import sys
import json
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment variables from .env
load_dotenv()

def parse_natural_language_to_json(user_input: str):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not found in .env")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    # Use today's date for relative time resolving
    current_date = datetime.now().strftime("%Y-%m-%d")

    # Read fetched.json to provide context to the LLM
    fetched_data = ""
    fetched_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetched.json")
    if os.path.exists(fetched_file):
        try:
            with open(fetched_file, "r", encoding="utf-8") as f:
                fetched_data = f.read()
        except Exception as e:
            print(f"Warning: Could not read fetched.json: {e}")
    
    prompt = f"""
You are an assistant that extracts time-tracking information from natural language.
Today's date is {current_date}. 
When the user mentions dates like '9th march', resolve them into the 'YYYY-MM-DD' format.
Note: In some edge cases, if the user specifically implied a mapping in their prompt like "9th march" -> "2026-05-09", you should deduce the intended dates. Otherwise, just use the literal date for the current year (e.g. 9th march -> {datetime.now().year}-03-09).

You MUST strictly map the tasks mentioned by the user to the exact task names provided in the fetched data below.
Do not invent task names. Pick the closest matching task "name" from the fetched data.

Fetched Data:
{fetched_data}

Extract the tasks, their dates, and duration in hours from the following text.
Output MUST be a valid JSON object matching the following structure exactly:

{{
  "tasks": [
    {{
      "task": "Task Name",
      "date": "YYYY-MM-DD",
      "duration_hours": 8
    }}
  ]
}}

User Input: {user_input}
"""

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        
        json_text = response.text
        if not json_text:
            print("Error: Received empty response from Gemini API.")
            return
            
        # Clean up potential markdown formatting
        clean_json_text = json_text.strip()
        if clean_json_text.startswith("```json"):
            clean_json_text = clean_json_text[7:]
        if clean_json_text.endswith("```"):
            clean_json_text = clean_json_text[:-3]
        clean_json_text = clean_json_text.strip()

        # Verify it's valid JSON
        parsed_json = json.loads(clean_json_text)
        
        # Write to input.json
        output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "input.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(parsed_json, f, indent=2)
        
        print(f"Successfully generated input.json with {len(parsed_json.get('tasks', []))} tasks.")
        print(json.dumps(parsed_json, indent=2))
            
    except Exception as e:
        print(f"API Request failed: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        user_input = " ".join(sys.argv[1:])
    else:
        user_input = input("Enter your time-tracking log: ")
    
    if user_input.strip():
        parse_natural_language_to_json(user_input)
    else:
        print("No input provided.")
        input_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "input.json")
        if os.path.exists(input_file):
            print("Using existing input.json to proceed further.")
        else:
            print("Error: input.json does not exist. Please provide input.")
            sys.exit(1)
