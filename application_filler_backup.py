import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import json
import os
import time
from dotenv import load_dotenv
import re
from bs4 import BeautifulSoup
import random
from datetime import datetime
import google.generativeai as genai
from trello import TrelloClient
import cv2
import numpy as np
from PIL import Image
import io

# Import shared functions
from linkedin_scrapper import sign_in, find_scrollable_container, scroll_and_get_jobs, load_cv_text

# Constants
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SAVED_JOBS_URL = "https://www.linkedin.com/my-items/saved-jobs/?cardType=SAVED"
CV_FILE_PATH = os.path.join(SCRIPT_DIR, "cv_text.txt")
RESUME_FILE_PATH = os.path.join(SCRIPT_DIR, "resume.pdf")
FORM_DATA_PATH = os.path.join(SCRIPT_DIR, "form_data.json")

# Test mode - set to True for quick testing with first 3 jobs
TEST_MODE = True
TEST_SKIP_RELEVANCE = True  # Skip relevance check in test mode to test apply button
DRY_RUN = True  # If True, fills forms but doesn't submit (good for testing)
TEST_URL = None  # Set to None to process LinkedIn jobs normally

# Load environment variables
load_dotenv()
print("✅ Environment variables loaded")

# Configure Gemini
gemini_api_key = os.getenv('GEMINI_API_KEY')
if not gemini_api_key:
    print("⚠️ GEMINI_API_KEY not found in environment variables. AI features will be disabled.")
    gemini_model = None
else:
    genai.configure(api_key=gemini_api_key)
    gemini_model = genai.GenerativeModel('gemini-2.5-pro')
    print("✅ Gemini client initialized with model: gemini-2.5-pro")

# Initialize Gemini - Use environment variable only
if not gemini_api_key:
    print("❌ GEMINI_API_KEY is required but not found in environment variables.")
    print("Please set the GEMINI_API_KEY environment variable and try again.")
    gemini_model = None
else:
    NEW_GEMINI_MODEL_ID = "gemini-2.5-pro"
    genai.configure(api_key=gemini_api_key)
    
    try:
        gemini_model = genai.GenerativeModel(NEW_GEMINI_MODEL_ID)
        print("✅ Gemini model initialized successfully")
    except Exception as e:
        print(f"❌ Failed to initialize Gemini model: {str(e)}")
        gemini_model = None

def apply_fallback_radio_selections(questions_data, form_data):
    """Apply fallback intelligent radio selections when LLM fails"""
    selections = {}
    
    for q_data in questions_data:
        question = q_data['question']
        options = q_data['options']
        question_type = q_data.get('question_type', 'radio')
        
        print(f"🔄 Applying fallback for: {question}")
        print(f"   Options: {options}")
        
        # Create formatted options for intelligent selection
        formatted_options = []
        for opt in options:
            formatted_options.append({
                "label": opt,
                "label_lower": opt.lower(),
                "element": None  # Not needed for fallback
            })
        
        # Use the existing intelligent selection logic
        selected = select_radio_option_intelligently(question.lower(), formatted_options, form_data)
        
        if selected:
            selections[question] = selected["label"]
            print(f"   ✅ Fallback selected: {selected['label']}")
        else:
            # Last resort: pick first non-placeholder option
            valid_options = [opt for opt in options if not any(word in opt.lower() for word in ["select", "choose", "---"])]
            if valid_options:
                selections[question] = valid_options[0]
                print(f"   ⚠️ Default selected: {valid_options[0]}")
    
    return selections

def analyze_form_questions_with_llm(questions_data, cv_content, job_description=""):
    """
    Analyze all form questions using LLM based on CV content and job context.
    
    Args:
        questions_data: List of dicts with 'question', 'options', 'question_type'
        cv_content: The CV text content
        job_description: Optional job description for context
    
    Returns:
        Dict mapping question -> selected answer
    """
    if not gemini_model:
        print("❌ Gemini model not available for form analysis")
        return {}
    
    if not questions_data:
        print("ℹ️ No questions to analyze")
        return {}
    
    print(f"\n🧠 Analyzing {len(questions_data)} form questions with LLM...")
    
    # Format questions for the prompt
    formatted_questions = []
    for i, q_data in enumerate(questions_data, 1):
        question_text = q_data['question']
        options = q_data['options']
        question_type = q_data.get('question_type', 'radio')
        
        options_text = "\n".join([f"   - {opt}" for opt in options])
        
        formatted_questions.append(f"""
Question {i} (Type: {question_type}):
{question_text}
Options:
{options_text}
""")
    
    prompt = f"""Fill job application form based on candidate's CV.

CV: {cv_content[:2000]}

Job: {job_description[:800] if job_description else "Not provided"}

Questions:
{"".join(formatted_questions)}

CRITICAL RULES:
- Immigration: No sponsorship needed (EU citizen) - Answer NO to sponsorship questions
- Work arrangements: ALWAYS ACCEPT HYBRID AND REMOTE WORK
  * For ANY question about hybrid work (including "3 days office, 2 days WFH"), answer YES/Accept/Agree
  * For ANY question about remote work, answer YES/Accept/Agree  
  * For ANY question about flexible work arrangements, answer YES/Accept/Agree
  * NEVER answer NO to hybrid or remote work questions
- Availability: Immediate start (unemployed) - Answer YES to immediate availability
- Experience: 4+ years data science, Python, SQL - Select appropriate experience levels (3+ years, 2-3 years)
- English: Advanced/Native level - Select advanced/fluent options
- Salary: Open to competitive offers - Select appropriate salary ranges

ABSOLUTELY CRITICAL: If you see a question like "Glovo's hybrid ways of working mean 3 days in the office, and 2 days WFH, does this match your preferences or requirements?" - ALWAYS answer "Yes".

Format (exact text match):
Question 1: [Selected Option]
Question 2: [Selected Option]
etc.

Answer each question:"""

    try:
        print("⚡ Sending form analysis request to Gemini...")
        print(f"🔍 Prompt preview (first 500 chars):")
        print(prompt[:500] + "..." if len(prompt) > 500 else prompt)
        print("=" * 50)
        
        response = gemini_model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.1,  # Low temperature for consistent, factual answers
                max_output_tokens=4096  # Increased token limit to avoid truncation
            )
        )
        
        print(f"📋 Response received - has parts: {bool(response.parts)}")
        if hasattr(response, 'prompt_feedback'):
            print(f"🔄 Prompt feedback: {response.prompt_feedback}")
        
        if response.parts:
            response_text = "".join(part.text for part in response.parts)
            print(f"📝 Response length: {len(response_text)} characters")
        else:
            print("⚠️ Empty response from Gemini - checking for blocks")
            if hasattr(response, 'candidates') and response.candidates:
                for i, candidate in enumerate(response.candidates):
                    print(f"   Candidate {i}: {candidate}")
                    if hasattr(candidate, 'finish_reason'):
                        print(f"   Finish reason: {candidate.finish_reason}")
            return {}
        
        print("\n📝 LLM Response for Form Questions:")
        print("=" * 50)
        print(response_text)
        print("=" * 50)
        
        # Parse the response
        selections = {}
        lines = response_text.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if line.startswith('Question ') and ':' in line:
                try:
                    # Extract question number and selected option
                    parts = line.split(':', 1)
                    question_num = int(parts[0].replace('Question ', '').strip())
                    selected_option = parts[1].strip()
                    
                    # Clean up the selected option
                    if selected_option.startswith('[') and selected_option.endswith(']'):
                        selected_option = selected_option[1:-1]
                    
                    # Map back to the original question
                    if 1 <= question_num <= len(questions_data):
                        original_question = questions_data[question_num - 1]['question']
                        
                        # Verify the selected option exists in the available options
                        available_options = questions_data[question_num - 1]['options']
                        
                        # Try exact match first
                        if selected_option in available_options:
                            selections[original_question] = selected_option
                            print(f"✅ Q{question_num}: {selected_option}")
                        else:
                            # Try fuzzy matching for case-insensitive or partial matches
                            best_match = None
                            for option in available_options:
                                if selected_option.lower() in option.lower() or option.lower() in selected_option.lower():
                                    best_match = option
                                    break
                            
                            if best_match:
                                selections[original_question] = best_match
                                print(f"✅ Q{question_num}: {best_match} (matched from '{selected_option}')")
                            else:
                                print(f"⚠️ Q{question_num}: Could not match '{selected_option}' to available options: {available_options}")
                
                except (ValueError, IndexError) as e:
                    print(f"⚠️ Error parsing line: {line} - {str(e)}")
                    continue
        
        print(f"\n✅ Successfully analyzed {len(selections)} questions")
        return selections
        
    except Exception as e:
        print(f"❌ Error in LLM form analysis: {str(e)}")
        return {}

def analyze_job_relevance_with_gemini(job_description, cv_text):
    """Analyze job relevance using Gemini API"""
    if not gemini_model:
        print("⚠️ Skipping job relevance analysis - Gemini client not available")
        return {"relevant": True, "reason": "Analysis skipped - Gemini API not available"}
    
    try:
        prompt = f"""
        Analyze the following job description based on my CV and determine if it's a good fit.
        My CV highlights: {cv_text}
        Job Description: {job_description}

        Return a JSON object with these fields:
        - "relevant" (boolean): True if it's a good fit, false otherwise.
        - "reason" (string): A brief explanation of your decision.
        """
        print("⚡ Sending request to Gemini API for relevance analysis...")
        print(f"🔍 Prompt length: {len(prompt)} characters")
        
        response = gemini_model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2
            )
        )
        
        print(f"📝 Raw Gemini response received")
        if response.parts:
            response_text = "".join(part.text for part in response.parts)
            print(f"✅ Response text length: {len(response_text)} characters")
            print(f"📄 Response preview: {response_text[:200]}...")
        else:
            print("⚠️ Gemini API response was empty or blocked.")
            if hasattr(response, 'prompt_feedback'):
                print(f"📋 Prompt Feedback: {response.prompt_feedback}")
            response_text = '{"relevant": true, "reason": "No response from API or content blocked"}'
        
        # Clean and parse the JSON response
        cleaned_response = response_text.strip().replace('```json', '').replace('```', '')
        return json.loads(cleaned_response)

    except json.JSONDecodeError as e:
        print(f"❌ JSON parsing error: {str(e)}")
        print(f"📄 Raw response that failed to parse: {response_text if 'response_text' in locals() else 'No response'}")
        return {"relevant": True, "reason": "JSON parsing failed - proceeding anyway"}
    except Exception as e:
        print(f"❌ Gemini API error during relevance analysis: {str(e)}")
        print(f"🔍 Error type: {type(e).__name__}")
        if hasattr(e, 'response'):
            print(f"📋 Error response: {e.response}")
        return {"relevant": True, "reason": "Analysis failed - proceeding anyway"}

def get_phone_number_for_country(country_name):
    """Get appropriate phone number format based on country"""
    # Extract base digits from CV phone (654808087)
    cv_content = ""
    try:
        cv_path = os.path.join(os.path.dirname(__file__), "cv_text.txt")
        with open(cv_path, 'r', encoding='utf-8') as f:
            cv_content = f.read()
    except:
        pass
    
    # Extract just the digits from CV phone
    import re
    phone_pattern = r'\+\d{1,4}\s?(\d{3})\s?(\d{3})\s?(\d{3})'
    phone_match = re.search(phone_pattern, cv_content)
    base_digits = "654808087"  # fallback
    
    if phone_match:
        base_digits = phone_match.group(1) + phone_match.group(2) + phone_match.group(3)
    
    # Format according to country
    country_lower = country_name.lower()
    
    if any(word in country_lower for word in ["spain", "españa", "es"]):
        return f"+34 {base_digits[:3]} {base_digits[3:6]} {base_digits[6:9]}"
    elif any(word in country_lower for word in ["united kingdom", "uk", "britain", "gb"]):
        return f"+44 {base_digits[:4]} {base_digits[4:7]} {base_digits[7:10]}"
    elif any(word in country_lower for word in ["germany", "deutschland", "de"]):
        return f"+49 {base_digits[:3]} {base_digits[3:6]} {base_digits[6:9]}"
    elif any(word in country_lower for word in ["france", "francia", "fr"]):
        return f"+33 {base_digits[:1]} {base_digits[1:3]} {base_digits[3:5]} {base_digits[5:7]} {base_digits[7:9]}"
    elif any(word in country_lower for word in ["netherlands", "holland", "nl"]):
        return f"+31 {base_digits[:1]} {base_digits[1:4]} {base_digits[4:6]} {base_digits[6:8]}"
    else:
        # Default to Spanish format for unknown countries
        return f"+34 {base_digits[:3]} {base_digits[3:6]} {base_digits[6:9]}"


def get_appropriate_phone_number(driver, form_data):
    """Get phone number formatted for the selected country"""
    try:
        # First try to detect what country was selected in the form
        try:
            country_dropdowns = driver.find_elements(By.CSS_SELECTOR, "select")
            for dropdown in country_dropdowns:
                try:
                    context = get_field_context(driver, dropdown)
                    if any(word in context.lower() for word in ["country", "location"]):
                        from selenium.webdriver.support.ui import Select
                        select = Select(dropdown)
                        selected_country = select.first_selected_option.text
                        print(f"🌍 Detected selected country in form: {selected_country}")
                        formatted_phone = get_phone_number_for_country(selected_country)
                        print(f"📞 Formatted phone for {selected_country}: {formatted_phone}")
                        return formatted_phone
                except:
                    continue
        except:
            pass
        
        # Fallback: Load CV content to extract current phone number
        cv_content = ""
        try:
            cv_path = os.path.join(os.path.dirname(__file__), "cv_text.txt")
            with open(cv_path, 'r', encoding='utf-8') as f:
                cv_content = f.read()
        except Exception as e:
            print(f"⚠️ Could not load CV content: {str(e)}")
        
        # Extract phone number from CV using regex
        import re
        phone_pattern = r'\+\d{1,4}\s?\d{3}\s?\d{3}\s?\d{3}'
        phone_match = re.search(phone_pattern, cv_content)
        
        if phone_match:
            cv_phone = phone_match.group().strip()
            print(f"📞 Found phone number in CV: {cv_phone}")
            return cv_phone
        else:
            print("⚠️ No phone number found in CV, using fallback")
            return "+34 654 808 087"  # Fallback from CV content
        
    except Exception as e:
        print(f"⚠️ Error extracting phone from CV: {str(e)}")
        return "+34 654 808 087"  # Safe fallback

def get_country_from_cv():
    """Extract country information from CV text"""
    try:
        cv_path = os.path.join(os.path.dirname(__file__), "cv_text.txt")
        with open(cv_path, 'r', encoding='utf-8') as f:
            cv_content = f.read().lower()
        
        # Country detection based on CV content
        country_mapping = {
            "spain": "Spain",
            "españa": "Spain", 
            "barcelona": "Spain",
            "catalonia": "Spain",
            "madrid": "Spain",
            "united kingdom": "United Kingdom",
            "uk": "United Kingdom",
            "london": "United Kingdom",
            "manchester": "United Kingdom",
            "germany": "Germany",
            "deutschland": "Germany",
            "berlin": "Germany",
            "munich": "Germany",
            "france": "France",
            "paris": "France",
            "lyon": "France",
            "netherlands": "Netherlands",
            "amsterdam": "Netherlands",
            "rotterdam": "Netherlands"
        }
        
        for keyword, country in country_mapping.items():
            if keyword in cv_content:
                print(f"🌍 Detected country from CV: {country} (keyword: {keyword})")
                return country
        
        print("🌍 No country detected from CV, using Spain as default")
        return "Spain"  # Default based on current CV
        
    except Exception as e:
        print(f"⚠️ Error detecting country from CV: {str(e)}")
        return "Spain"

def get_salary_for_job(form_data, job_analysis):
    """Get salary value for this specific job, using LLM estimation if available"""
    if job_analysis and job_analysis.get('salary_estimation'):
        salary_range = job_analysis['salary_estimation']
        print(f"💰 Using LLM-estimated salary: {salary_range}")
        
        # Extract the middle or lower bound of the range
        # Pattern: "55000-70000 EUR" -> use 60000 or 55000
        import re
        numbers = re.findall(r'\d+', salary_range)
        if len(numbers) >= 2:
            # Use the lower bound to be conservative
            estimated_salary = numbers[0]
            print(f"💡 Using conservative estimate: {estimated_salary}")
            return estimated_salary
        elif len(numbers) == 1:
            return numbers[0]
    
    # Fallback to default salary from form_data
    fallback_salary = form_data["professional"]["desired_salary"]
    print(f"💰 Using default salary: {fallback_salary}")
    return fallback_salary


def load_form_data():
    """Load or create form data template with keyword-based answers"""
    if os.path.exists(FORM_DATA_PATH):
        with open(FORM_DATA_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    # Create template - LLM now handles all form questions intelligently
    template = {
        "personal": {
            "name": "Jessie Lee Delgadillo Newman",
            "email": "jonathan9879@gmail.com", 
            "phone": "+34 654 808 087",
            "location": "Barcelona, Spain",
            "linkedin": "https://linkedin.com/in/jessie-delgadillo-data-scientist",
            "website": "",
            "github": ""
        },
        "professional": {
            "years_experience": "4",
            "current_title": "Data Scientist",
            "desired_salary": "50000",
            "salary_currency": "EUR",
            "notice_period": "1 month",
            "education_level": "Master's Degree",
            "languages": ["English (Fluent)", "Spanish (Native)"]
        },
        "questions": {
            "why_company": "I'm excited about innovative companies focused on data-driven solutions",
            "why_role": "This role aligns perfectly with my data science experience",
            "biggest_achievement": "Implemented ML models that improved business efficiency by 35%"
        }
    }
    
    with open(FORM_DATA_PATH, 'w', encoding='utf-8') as f:
        json.dump(template, f, indent=2)
    return template

def find_keyword_answer(question, form_data):
    """Legacy function - now returns None so LLM handles all form questions intelligently"""
    # All form questions are now handled by the LLM analysis system
    # This function is kept for compatibility but returns None to force LLM usage
    return None

def analyze_form_field(field_element):
    """Analyze a form field to determine its type and required data"""
    field_info = {
        'type': None,
        'name': None,
        'required': False,
        'options': None
    }
    
    try:
        # Get field attributes
        field_type = field_element.get_attribute('type')
        field_name = field_element.get_attribute('name')
        field_id = field_element.get_attribute('id')
        placeholder = field_element.get_attribute('placeholder')
        label = None
        
        # Try to find associated label
        try:
            if field_id:
                label = field_element.find_element(By.XPATH, f"//label[@for='{field_id}']").text
            else:
                # Try finding nearby label
                label = field_element.find_element(By.XPATH, "./preceding::label[1]").text
        except:
            pass
        
        # Determine field type
        if field_type in ['text', 'email', 'tel', 'url']:
            field_info['type'] = 'text'
        elif field_type == 'number':
            field_info['type'] = 'number'
        elif field_type == 'radio':
            field_info['type'] = 'radio'
            field_info['options'] = get_radio_options(field_element)
        elif field_element.tag_name == 'select':
            field_info['type'] = 'select'
            field_info['options'] = get_select_options(field_element)
        elif field_element.tag_name == 'textarea':
            field_info['type'] = 'textarea'
        
        # Set name using best available identifier
        field_info['name'] = label or field_name or placeholder or field_id
        
        # Check if required
        field_info['required'] = (
            field_element.get_attribute('required') is not None or
            'required' in (field_element.get_attribute('class') or '')
        )
        
        print(f"Found field: {field_info}")
        return field_info
        
    except Exception as e:
        print(f"Error analyzing field: {str(e)}")
        return None

def get_form_fields(driver):
    """Find and analyze all form fields on the page"""
    form_fields = []
    
    # Common form field selectors
    selectors = [
        "input[type='text']",
        "input[type='email']",
        "input[type='tel']",
        "input[type='number']",
        "input[type='url']",
        "input[type='radio']",
        "select",
        "textarea"
    ]
    
    for selector in selectors:
        fields = driver.find_elements(By.CSS_SELECTOR, selector)
        for field in fields:
            field_info = analyze_form_field(field)
            if field_info:
                form_fields.append(field_info)
    
    return form_fields

def handle_cookies_popup(driver):
    """Handle various types of cookie consent and privacy preference popups"""
    try:
        # Enhanced consent buttons with more patterns
        consent_button_selectors = [
            # Consent buttons
            "button.fc-button-consent",
            "button.fc-cta-consent",
            "button[aria-label='Consentir']",
            ".fc-button-label",
            # Common patterns
            "button[contains(text(), 'Consent')]",
            "button[contains(text(), 'Consentir')]",
            "button[contains(text(), 'Agree')]",
            "button[contains(@class, 'consent')]",
            
            # Additional patterns from real sites
            "button:contains('Accept All')",
            "button:contains('Accept all')",
            "button:contains('Aceptar todo')",
            "button:contains('Aceptar todas')",
            "button[class*='primary']",
            "button[class*='accept']",
            "button[id*='accept']",
            "button[data-qa*='accept']",
            "button[data-testid*='accept']",
            "button[data-testid*='consent']",
            
            # Glovo and similar sites
            ".consent-accept",
            ".cookie-accept", 
            ".gdpr-accept",
            "#consent-accept",
            "#cookie-accept",
            "#gdpr-accept"
        ]
        
        # Try consent buttons first
        for selector in consent_button_selectors:
            try:
                button = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
                print(f"✅ Clicking consent button: {button.text}")
                button.click()
                time.sleep(1)
                return True
            except:
                continue
                
        # First check for OneTrust privacy center
        if "OneTrust" in driver.page_source or "onetrust" in driver.page_source.lower():
            print("🔍 Detected OneTrust privacy center")
            
            # Try to find and click "Allow All" or "Accept All" first
            accept_buttons = [
                "button#onetrust-accept-btn-handler",
                "button[id*='accept-recommended']",
                "button[contains(text(), 'Allow All')]",
                "#accept-recommended-btn-handler"
            ]
            
            for selector in accept_buttons:
                try:
                    button = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                    print(f"✅ Clicking accept all button: {button.text}")
                    button.click()
                    time.sleep(1)
                    return True
                except:
                    continue
            
            # If accept all fails, try confirm choices
            confirm_buttons = [
                "button.save-preference-btn-handler",
                "button.onetrust-close-btn-handler",
                "button[contains(text(), 'Confirm')]"
            ]
            
            for selector in confirm_buttons:
                try:
                    button = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                    print(f"✅ Clicking confirm choices button: {button.text}")
                    button.click()
                    time.sleep(1)
                    return True
                except:
                    continue
        
        # Generic cookie/privacy buttons
        cookie_button_selectors = [
            # Previous selectors...
            # Additional OneTrust selectors
            "button.ot-pc-refuse-all-handler",  # Reject all
            "button.ot-pc-refuse-all-handler ~ button",  # Allow all (sibling)
            # iframes
            "iframe[title*='Cookie']",
            "iframe[id*='cookie']",
            "iframe[title*='Privacy']",
            # Additional language variations
            "button[contains(text(), 'Aceptar todas')]",
            "button[contains(text(), 'Accepter tout')]",
            "button[contains(text(), 'Akzeptieren')]"
        ]
        
        # Check for and switch to cookie iframe if present
        iframes = driver.find_elements(By.CSS_SELECTOR, 
            "iframe[title*='Cookie'], iframe[id*='cookie'], iframe[title*='Privacy']"
        )
        
        if iframes:
            print("🔍 Found cookie iframe, switching context...")
            driver.switch_to.frame(iframes[0])
        
        for selector in cookie_button_selectors:
            try:
                button = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
                print(f"🍪 Found cookie/privacy button: {button.text}")
                button.click()
                time.sleep(1)
                
                # Switch back to default content if we were in an iframe
                if iframes:
                    driver.switch_to.default_content()
                return True
            except:
                continue
        
        # Switch back to default content if we were in an iframe
        if iframes:
            driver.switch_to.default_content()
            
        return False
            
    except Exception as e:
        print(f"⚠️ Error handling cookie popup: {str(e)}")
        # Make sure we're back in the main frame
        driver.switch_to.default_content()
        return False

def find_saved_jobs_container(driver):
    """Find the container with saved jobs list"""
    try:
        container = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.workflow-results-container ul[role='list']"))
        )
        print("✅ Found saved jobs container")
        return container
    except Exception as e:
        print(f"❌ Could not find saved jobs container: {str(e)}")
        return None

def extract_job_ids_from_page(driver):
    """Extract job IDs from current page of saved jobs"""
    job_ids = []
    try:
        job_cards = driver.find_elements(
            By.CSS_SELECTOR,
            "div[data-chameleon-result-urn*='fsd_jobPosting']"
        )
        
        for card in job_cards:
            try:
                urn = card.get_attribute('data-chameleon-result-urn')
                if urn and 'fsd_jobPosting' in urn:
                    job_id = urn.split(':')[-1]
                    job_ids.append(job_id)
                    print(f"✅ Found job ID: {job_id}")
            except Exception as e:
                print(f"❌ Error extracting job ID from card: {str(e)}")
                continue
                
        return list(set(job_ids)) # Return unique IDs
    except Exception as e:
        print(f"❌ Error finding job cards: {str(e)}")
        return []

def scroll_saved_jobs(driver):
    """Scroll through saved jobs list with mouse-like behavior"""
    try:
        container = find_saved_jobs_container(driver)
        if not container:
            return
        last_height = driver.execute_script("return arguments[0].scrollHeight", container)
        while True:
            driver.execute_script("arguments[0].scrollTo({top: arguments[0].scrollHeight, behavior: 'smooth'});", container)
            time.sleep(random.uniform(1, 3))
            new_height = driver.execute_script("return arguments[0].scrollHeight", container)
            if new_height == last_height:
                break
            last_height = new_height
        print("✅ Finished scrolling saved jobs")
    except Exception as e:
        print(f"❌ Error scrolling saved jobs: {str(e)}")

def go_to_next_page(driver):
    """Click next page button if available"""
    try:
        next_button = driver.find_element(
            By.CSS_SELECTOR,
            "button.artdeco-pagination__button--next:not(.artdeco-button--disabled)"
        )
        if next_button:
            next_button.click()
            time.sleep(2)
            return True
    except:
        return False
    return False

def extract_job_description(driver):
    """Extract job description from the job details page"""
    try:
        # Wait for job details to load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((
                By.CSS_SELECTOR, 
                "div.jobs-description-content__text--stretch"
            ))
        )
        
        # Click "See more" if present
        try:
            see_more_button = driver.find_element(
                By.CSS_SELECTOR,
                "button.jobs-description__footer-button"
            )
            if see_more_button.is_displayed():
                see_more_button.click()
                time.sleep(1)  # Wait for content to expand
        except:
            pass  # Button might not exist if content is already expanded
        
        # Get the full description
        description_element = driver.find_element(
            By.CSS_SELECTOR,
            "div.jobs-box__html-content.jobs-description-content__text--stretch"
        )
        
        description_text = description_element.text
        print("✅ Extracted job description")
        return description_text
            
    except Exception as e:
        print(f"❌ Could not extract job description: {str(e)}")
        return None

def analyze_application_forms(driver):
    """Find and analyze forms on the application page"""
    forms_found = []
    
    # Common form field selectors with labels
    field_types = {
        "input[type='text']": "Text input",
        "input[type='email']": "Email input",
        "input[type='tel']": "Phone input",
        "input[type='number']": "Number input",
        "input[type='url']": "URL input",
        "input[type='file']": "File upload",
        "textarea": "Text area",
        "select": "Dropdown"
    }
    
    for selector, field_type in field_types.items():
        fields = driver.find_elements(By.CSS_SELECTOR, selector)
        for field in fields:
            try:
                # Try to find associated label
                label = ""
                
                # Check for aria-label
                label = field.get_attribute("aria-label") or ""
                
                # Check for placeholder
                if not label:
                    label = field.get_attribute("placeholder") or ""
                
                # Check for nearby label element
                if not label:
                    try:
                        # Look for label before the field
                        label_elem = field.find_element(By.XPATH, "./preceding::label[1]")
                        label = label_elem.text
                    except:
                        pass
                
                if label:
                    forms_found.append({
                        "type": field_type,
                        "label": label,
                        "required": field.get_attribute("required") == "true"
                    })
                    print(f"📝 Found {field_type}: {label} {'(Required)' if field.get_attribute('required') else ''}")
                    
            except Exception as e:
                continue
    
    return forms_found

def generate_answer(question, job_description, cv_text):
    model = genai.GenerativeModel('gemini-1.5-pro-latest')
    prompt = f"""Based on my CV: {cv_text}\nJob description: {job_description}\nAnswer this question concisely and appropriately: {question}"""
    response = model.generate_content(prompt)
    return response.text.strip()

def is_newsletter_or_notification_form(driver, form_sections):
    """
    Detect if the current form is a newsletter signup or notification form
    that should be skipped to continue to the actual application.
    
    Returns True if this appears to be a newsletter/notification form that should be skipped.
    """
    try:
        # Count actual form fields (not just text/labels)
        input_fields = []
        field_contexts = []
        
        for section in form_sections:
            try:
                # Look for actual input fields
                inputs = section.find_elements(By.CSS_SELECTOR, 'input[type="text"], input[type="email"], input[type="tel"], textarea, select')
                for inp in inputs:
                    if inp.is_displayed():
                        # Get context for this field
                        try:
                            label_elem = section.find_element(By.TAG_NAME, 'label')
                            context = label_elem.text.lower().strip()
                            input_fields.append(inp)
                            field_contexts.append(context)
                        except:
                            # Try to get context from placeholder or nearby text
                            placeholder = inp.get_attribute('placeholder') or ''
                            name = inp.get_attribute('name') or ''
                            field_contexts.append((placeholder + ' ' + name).lower())
                            input_fields.append(inp)
            except:
                continue
        
        print(f"🔍 Found {len(input_fields)} form fields with contexts: {field_contexts}")
        
        # If only 1 field, check if it's likely a newsletter signup
        if len(input_fields) == 1:
            context = field_contexts[0] if field_contexts else ""
            
            # Newsletter/notification indicators
            newsletter_indicators = [
                'email', 'newsletter', 'notification', 'updates', 'alerts', 
                'subscribe', 'marketing', 'promotional', 'comunicaciones',
                'boletín', 'correo', 'notificaciones'
            ]
            
            # Check if the single field context suggests newsletter/notifications
            is_likely_newsletter = any(indicator in context for indicator in newsletter_indicators)
            
            if is_likely_newsletter:
                print(f"🚫 Detected single-field newsletter/notification form: '{context}'")
                
                # Look for additional context clues on the page
                try:
                    page_text = driver.find_element(By.CSS_SELECTOR, '.jobs-easy-apply-modal').text.lower()
                    newsletter_page_indicators = [
                        'newsletter', 'updates', 'notifications', 'marketing', 
                        'promotional', 'subscribe', 'communications', 'alerts',
                        'boletín', 'actualizaciones', 'comunicaciones'
                    ]
                    
                    if any(indicator in page_text for indicator in newsletter_page_indicators):
                        print("✅ Page content confirms this is a newsletter/notification form")
                        return True
                except:
                    pass
                
                # If we're unsure, check if there's an "Apply" or "Continue" button that suggests this isn't the main form
                try:
                    buttons = driver.find_elements(By.CSS_SELECTOR, '.jobs-easy-apply-modal button.artdeco-button')
                    for btn in buttons:
                        btn_text = btn.text.lower().strip()
                        if any(keyword in btn_text for keyword in ['apply', 'start', 'begin', 'aplicar', 'comenzar']):
                            print(f"🔍 Found '{btn_text}' button - this suggests we need to skip this form and continue")
                            return True
                except:
                    pass
                
                print("⚠️ Single field found but unclear if newsletter - proceeding cautiously")
                return False
        
        # If multiple fields, it's likely a real application form
        elif len(input_fields) > 1:
            print("✅ Multiple form fields detected - this appears to be a real application form")
            return False
        
        # If no fields, something's wrong but don't skip
        else:
            print("⚠️ No form fields detected")
            return False
            
    except Exception as e:
        print(f"⚠️ Error analyzing form type: {str(e)}")
        return False

def handle_easy_apply(driver, job_description, cv_text, form_data):
    try:
        # Click Easy Apply button
        easy_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.jobs-apply-button[aria-label*='Easy Apply']")))
        easy_button.click()

        # Wait for modal
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, 'jobs-easy-apply-modal')))

        page_count = 0
        max_pages = 10  # Prevent infinite loops
        
        while page_count < max_pages:
            page_count += 1
            print(f"\n📄 Processing Easy Apply page {page_count}")
            
            # Handle cookies popup on each page
            handle_cookies_popup(driver)
            time.sleep(1)
            
            # Find form elements
            form_sections = driver.find_elements(By.CLASS_NAME, 'jobs-easy-apply-form-section__element')
            
            # Check if this is a newsletter/notification form that should be skipped
            if is_newsletter_or_notification_form(driver, form_sections):
                print("⏭️ Skipping newsletter/notification form - looking for Apply/Continue button...")
                
                # Look for Apply/Continue button to proceed to actual application
                buttons = driver.find_elements(By.CSS_SELECTOR, '.jobs-easy-apply-modal button.artdeco-button')
                skipped_form = False
                
                for btn in buttons:
                    btn_text = btn.text.lower().strip()
                    if any(keyword in btn_text for keyword in ['apply', 'start', 'begin', 'continue', 'aplicar', 'comenzar', 'continuar', 'next']):
                        print(f"✅ Clicking '{btn_text}' to skip newsletter form and start application")
                        btn.click()
                        time.sleep(2)
                        skipped_form = True
                        break
                
                if skipped_form:
                    continue  # Go to next page
                else:
                    print("⚠️ Could not find button to skip newsletter form")
            
            # Collect all form questions for LLM analysis
            questions_data = []
            section_mappings = {}
            
            # First, find ALL text fields, textareas, dropdowns, and radio buttons in the modal
            all_text_fields = driver.find_elements(By.CSS_SELECTOR, '.jobs-easy-apply-modal input[type="text"], .jobs-easy-apply-modal textarea')
            all_dropdowns = driver.find_elements(By.CSS_SELECTOR, '.jobs-easy-apply-modal select')
            all_radio_groups = {}
            
            # Process radio buttons
            radio_buttons = driver.find_elements(By.CSS_SELECTOR, '.jobs-easy-apply-modal input[type="radio"]')
            for radio in radio_buttons:
                radio_name = radio.get_attribute('name')
                if radio_name:
                    if radio_name not in all_radio_groups:
                        all_radio_groups[radio_name] = []
                    all_radio_groups[radio_name].append(radio)
            
            print(f"🔍 Found {len(all_text_fields)} text fields, {len(all_dropdowns)} dropdowns, {len(all_radio_groups)} radio groups")
            
            # Process all text fields and textareas
            for field in all_text_fields:
                try:
                    if not field.is_displayed():
                        continue
                        
                    # Get field context (label, placeholder, etc.)
                    field_context = get_field_context(driver, field)
                    field_label = field_context.lower()
                    
                    print(f"📝 Processing text field: {field_context}")
                    
                    # Only fill basic contact info, use LLM for everything else
                    filled_basic = False
                    if 'full name' in field_label or ('name' in field_label and 'first' not in field_label and 'last' not in field_label):
                        field.clear()
                        field.send_keys(form_data['personal']['name'])
                        print(f"✅ Filled name field: {field_context}")
                        filled_basic = True
                    elif 'email' in field_label or 'correo' in field_label:
                        field.clear()
                        field.send_keys(form_data['personal']['email'])
                        print(f"✅ Filled email field: {field_context}")
                        filled_basic = True
                    elif 'phone' in field_label or 'mobile' in field_label or 'teléfono' in field_label:
                        field.clear()
                        field.send_keys(form_data['personal']['phone'])
                        print(f"✅ Filled phone field: {field_context}")
                        filled_basic = True
                    elif 'linkedin' in field_label and 'profile' in field_label:
                        field.clear()
                        field.send_keys(form_data['personal']['linkedin'])
                        print(f"✅ Filled LinkedIn field: {field_context}")
                        filled_basic = True
                    elif 'website' in field_label or 'portfolio' in field_label:
                        field.clear()
                        field.send_keys(form_data['personal']['website'])
                        print(f"✅ Filled website field: {field_context}")
                        filled_basic = True
                    # Remove auto-fill for experience, salary, and other complex fields
                    # Let LLM handle these for better context-aware answers
                    
                    # If not filled with basic matching, add to LLM analysis
                    if not filled_basic:
                        questions_data.append({
                            'question': field_context,
                            'options': [],
                            'question_type': 'text'
                        })
                        section_mappings[field_context] = {'element': field, 'type': 'text'}
                        print(f"📋 Added to LLM analysis: {field_context}")
                        
                except Exception as e:
                    print(f"⚠️ Error processing text field: {str(e)}")
                    continue
            
            # Process dropdowns
            for dropdown in all_dropdowns:
                try:
                    if not dropdown.is_displayed():
                        continue
                        
                    dropdown_context = get_field_context(driver, dropdown)
                    print(f"📋 Found dropdown: {dropdown_context}")
                    
                    # Collect dropdown options for LLM analysis
                    options = dropdown.find_elements(By.TAG_NAME, 'option')
                    option_texts = [opt.text.strip() for opt in options if opt.text.strip()]
                    
                    if option_texts:
                        questions_data.append({
                            'question': dropdown_context,
                            'options': option_texts,
                            'question_type': 'dropdown'
                        })
                        section_mappings[dropdown_context] = {'element': dropdown, 'type': 'dropdown'}
                        print(f"📋 Added dropdown to LLM analysis: {dropdown_context}")
                        
                except Exception as e:
                    print(f"⚠️ Error processing dropdown: {str(e)}")
                    continue
            
            # Process radio button groups
            for group_name, radios in all_radio_groups.items():
                try:
                    # Get question text from first radio button
                    question_text = extract_question_text_from_radio(driver, radios[0])
                    if not question_text:
                        question_text = group_name
                    
                    print(f"📋 Found radio group: {question_text}")
                    
                    # Collect radio options
                    option_texts = []
                    for radio in radios:
                        try:
                            radio_id = radio.get_attribute("id")
                            label_text = get_radio_label_text(driver, radio, radio_id)
                            if label_text:
                                option_texts.append(label_text)
                        except:
                            continue
                    
                    if option_texts:
                        questions_data.append({
                            'question': question_text,
                            'options': option_texts,
                            'question_type': 'radio'
                        })
                        section_mappings[question_text] = {'element': radios, 'type': 'radio'}
                        print(f"📋 Added radio group to LLM analysis: {question_text}")
                        
                except Exception as e:
                    print(f"⚠️ Error processing radio group: {str(e)}")
                    continue
            
            # Use LLM to answer collected questions
            if questions_data:
                print(f"\n🧠 Using LLM to answer {len(questions_data)} Easy Apply questions...")
                llm_answers = analyze_form_questions_with_llm(questions_data, cv_text, job_description)
                
                # Apply LLM answers
                for question, answer in llm_answers.items():
                    if question in section_mappings:
                        mapping = section_mappings[question]
                        element = mapping['element']
                        field_type = mapping['type']
                        
                        try:
                            if field_type == 'dropdown':
                                from selenium.webdriver.support.ui import Select
                                select = Select(element)
                                # Try to find matching option
                                for option in select.options:
                                    if answer.lower() in option.text.lower() or option.text.lower() in answer.lower():
                                        select.select_by_visible_text(option.text)
                                        print(f"✅ Selected dropdown option: {option.text}")
                                        break
                            elif field_type == 'radio':
                                # Find matching radio button
                                selected = False
                                for radio in element:
                                    try:
                                        radio_id = radio.get_attribute("id")
                                        radio_label = get_radio_label_text(driver, radio, radio_id)
                                        if radio_label and (answer.lower() in radio_label.lower() or radio_label.lower() in answer.lower()):
                                            # Try multiple click methods
                                            try:
                                                radio.click()
                                            except:
                                                try:
                                                    driver.execute_script("arguments[0].click();", radio)
                                                except:
                                                    # Try clicking associated label
                                                    try:
                                                        label_elem = driver.find_element(By.CSS_SELECTOR, f"label[for='{radio_id}']")
                                                        label_elem.click()
                                                    except:
                                                        continue
                                            print(f"✅ Selected radio option: {radio_label}")
                                            selected = True
                                            break
                                    except:
                                        continue
                                
                                if not selected:
                                    print(f"⚠️ Could not find matching radio option for: {answer}")
                            elif field_type == 'text':
                                element.clear()
                                element.send_keys(answer)
                                print(f"✅ Filled text field with LLM: {question}")
                        except Exception as e:
                            print(f"⚠️ Error applying LLM answer for '{question}': {str(e)}")
            else:
                print("ℹ️ No questions found for LLM analysis on this page")

            # Find buttons for next step
            buttons = driver.find_elements(By.CSS_SELECTOR, '.jobs-easy-apply-modal button.artdeco-button')
            has_next = False
            
            for btn in buttons:
                btn_text = btn.text.lower().strip()
                if 'next' in btn_text or 'continue' in btn_text or 'siguiente' in btn_text:
                    print(f"➡️ Clicking '{btn_text}' to go to next page")
                    btn.click()
                    time.sleep(2)
                    has_next = True
                    break
                elif 'review' in btn_text or 'revisar' in btn_text:
                    print(f"📋 Clicking '{btn_text}' to review application")
                    btn.click()
                    time.sleep(2)
                    has_next = True
                    break
                elif 'submit' in btn_text or 'enviar' in btn_text:
                    print("⏸️ Ready to submit. Press Enter to continue or Ctrl+C to cancel...")
                    input("Press Enter to submit the application...")
                    btn.click()
                    time.sleep(2)
                    return True

            if not has_next:
                print("🏁 No more pages found - application process complete")
                break

        if page_count >= max_pages:
            print(f"⚠️ Reached maximum pages ({max_pages}) - stopping to prevent infinite loop")

        # Close modal if needed
        try:
            close_button = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Dismiss']")
            close_button.click()
        except:
            pass
        return False

    except Exception as e:
        print(f"❌ Error in Easy Apply: {str(e)}")
        return False

def generate_smart_dropdown_answer(question, job_description, cv_text, form_data):
    """Generate smart answers for dropdown questions using keywords first, then Gemini"""
    # First try keyword matching
    keyword_answer = find_keyword_answer(question, form_data)
    if keyword_answer:
        print(f"✅ Found keyword-based answer: {keyword_answer}")
        return keyword_answer
    
    # Fallback to basic logic for common dropdown questions
    question_lower = question.lower()
    if 'python' in question_lower:
        return "3-5 años" if 'años' in question_lower else "3-5 years"
    elif 'sql' in question_lower:
        return "3-5 años" if 'años' in question_lower else "3-5 years"
    elif 'ml' in question_lower or 'machine learning' in question_lower:
        return "2-3 años" if 'años' in question_lower else "2-3 years"
    elif 'data scientist' in question_lower:
        return "Sí" if any(spanish_word in question_lower for spanish_word in ['sí', 'años', 'experiencia']) else "Yes"
    
    # Only use Gemini for truly complex questions
    if not gemini_model:
        print(f"⚠️ No keyword match found for: {question}, using default")
        return "3 years" if 'years' in question_lower or 'años' in question_lower else "Yes"

    try:
        prompt = f"""
        Based on this CV: {cv_text[:500]}...
        
        Answer this dropdown question for a job application: "{question}"
        
        Provide a SHORT answer (1-3 words max) that would appear in a dropdown.
        For experience: "3-5 years" or "3-5 años"
        For yes/no: "Yes"/"No" or "Sí"/"No"
        """
        
        response = gemini_model.generate_content(prompt)
        if response.parts:
            answer = "".join(part.text for part in response.parts).strip()
            print(f"✅ Gemini generated answer: {answer}")
            return answer
        return None
        
    except Exception as e:
        print(f"Error generating dropdown answer: {str(e)}")
        return "Yes" if any(word in question_lower for word in ["do you", "can you", "¿"]) else "3 years"

def find_apply_button_visually(driver):
    """Find the Apply button using visual detection"""
    try:
        print("📸 Taking screenshot for visual button detection...")
        
        # Take screenshot
        screenshot = driver.get_screenshot_as_png()
        image = Image.open(io.BytesIO(screenshot))
        img_array = np.array(image)
        
        # Convert RGB to BGR for OpenCV
        img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
        
        # Define blue color ranges for LinkedIn's apply button
        # LinkedIn uses a specific blue: approximately #0A66C2
        lower_blue = np.array([180, 100, 50])   # Lower HSV range
        upper_blue = np.array([220, 255, 255])  # Upper HSV range
        
        # Convert to HSV for better color detection
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
        
        # Create mask for blue colors
        mask = cv2.inRange(hsv, lower_blue, upper_blue)
        
        # Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # Filter contours by area and aspect ratio (button-like shapes)
        button_candidates = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if 500 < area < 10000:  # Button size range
                x, y, w, h = cv2.boundingRect(contour)
                aspect_ratio = w / h
                if 1.5 < aspect_ratio < 8:  # Button-like aspect ratio
                    button_candidates.append((x, y, w, h, area))
        
        # Sort by area (larger buttons first)
        button_candidates.sort(key=lambda x: x[4], reverse=True)
        
        # Check each candidate by looking for "Apply" text nearby
        for x, y, w, h, area in button_candidates:
            # Expand region slightly to capture text
            text_region = img_bgr[max(0, y-10):y+h+10, max(0, x-10):x+w+10]
            
            # Convert to grayscale for text detection
            gray = cv2.cvtColor(text_region, cv2.COLOR_BGR2GRAY)
            
            # Simple text detection (look for high contrast areas that might be text)
            # We'll use a simple heuristic: if the region has the right color and size,
            # it's likely an Apply button
            center_x = x + w // 2
            center_y = y + h // 2
            
            print(f"🎯 Found potential Apply button at ({center_x}, {center_y}) - size: {w}x{h}")
            
            # Click on the center of the button
            actions = ActionChains(driver)
            
            # Move to coordinates and click
            # Note: We need to account for any page scrolling
            actions.move_by_offset(center_x, center_y).click().perform()
            
            # Reset mouse position
            actions.move_by_offset(-center_x, -center_y).perform()
            
            print(f"✅ Clicked visually detected button at ({center_x}, {center_y})")
            return True
        
        print("❌ No Apply button found visually")
        return False
        
    except Exception as e:
        print(f"❌ Visual detection error: {str(e)}")
        return False

def find_and_click_apply_button(driver, job_title=""):
    """Find and click the apply button using multiple methods including visual detection"""
    # Get the button using the most reliable selector first
    button_selectors = [
        "#jobs-apply-button-id",
        "button[data-live-test-job-apply-button]",
        "button.jobs-apply-button",
    ]
    
    button_element = None
    for selector in button_selectors:
        try:
            button_element = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
            print(f"✅ Found apply button with selector: {selector}")
            break
        except TimeoutException:
            continue
    
    if not button_element:
        print("❌ No apply button found with selectors")
        return False
    
    # Check if it's Easy Apply
    is_easy_apply = False
    button_text = button_element.text.lower().strip()
    aria_label = button_element.get_attribute('aria-label') or ''
            
    if "easy apply" in button_text or "easy apply" in aria_label.lower():
        is_easy_apply = True
                
    if is_easy_apply:
        print("✅ Detected Easy Apply button")
        return handle_easy_apply(driver, extract_job_description(driver), load_cv_text(CV_FILE_PATH), load_form_data())
    
    print(f"✅ Found external apply button: {button_text}")
    
    # Store original state
    original_window = driver.current_window_handle
    original_url = driver.current_url
    
    # Try visual detection first (most reliable for this case)
    print("🎯 Attempting visual button detection...")
    if find_apply_button_visually(driver):
        time.sleep(3)
        
        # Check if navigation occurred or new window opened
        if (len(driver.window_handles) > 1 or 
            driver.current_url != original_url or
            "apply" in driver.current_url.lower()):
            
            print("✅ Success with visual detection")
            
            # Handle new window if opened
            if len(driver.window_handles) > 1:
                for handle in driver.window_handles:
                    if handle != original_window:
                        driver.switch_to.window(handle)
                        break
                print("✅ Switched to new window")
            
            return True
    
    # Fallback to other methods if visual detection fails
    print("⚠️ Visual detection failed, trying other methods...")
    
    # Scroll button into view
    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", button_element)
    time.sleep(1)
    
    # Try other click methods
    click_methods = [
        ("Direct click", lambda: button_element.click()),
        ("JavaScript click", lambda: driver.execute_script("arguments[0].click();", button_element)),
        ("Dispatch event", lambda: driver.execute_script("""
            var element = arguments[0];
            var event = new MouseEvent('click', {
                view: window,
                bubbles: true,
                cancelable: true
            });
            element.dispatchEvent(event);
        """, button_element)),
        ("Focus and Enter", lambda: (button_element.send_keys(""), button_element.send_keys("\n"))),
    ]
    
    for method_name, method in click_methods:
        try:
            print(f"🔄 Trying: {method_name}")
            method()
            time.sleep(2)
            
            # Check if navigation occurred or new window opened
            if (len(driver.window_handles) > 1 or 
                driver.current_url != original_url or
                "apply" in driver.current_url.lower()):
                
                print(f"✅ Success with {method_name}")
                
                # Handle new window if opened
                if len(driver.window_handles) > 1:
                    for handle in driver.window_handles:
                        if handle != original_window:
                            driver.switch_to.window(handle)
                            break
                    print("✅ Switched to new window")
                    
                    # Check if we're on a job board with multiple listings - do this BEFORE any other processing
                    time.sleep(3)  # Give page time to load
                    handle_cookies_popup(driver)  # Handle cookies first
                    
                    # Priority: Job navigation before newsletter detection
                    if find_and_click_matching_job(driver, job_title):
                        print("✅ Found and clicked matching job on external site")
                        time.sleep(2)  # Wait for job page to load
                    else:
                        print("🔍 No job matching needed or job already selected")
                
                return True
                
        except Exception as e:
            print(f"❌ {method_name} failed: {str(e)}")
            continue
    
    print("❌ All click methods failed")
    return False

def find_and_click_matching_job(driver, job_title):
    """Find and click a job listing that matches the LinkedIn job title"""
    if not job_title or job_title == "Unknown Title":
        print("⚠️ No job title provided for matching")
        return False
    
    print(f"🔍 Looking for job matching: '{job_title}'")
    
    # Common selectors for job listings on external sites
    job_listing_selectors = [
        "a[href*='/job/']",  # Generic job links
        "a[href*='/jobs/']",
        "a[href*='/opening']",
        "a[href*='/career']",
        ".job-title",
        ".job-link", 
        ".position-title",
        "h2 a", "h3 a", "h4 a",  # Common heading links
        ".card a", ".job-card a",  # Card-based layouts
        "[role='link']",  # ARIA role links
    ]
    
    # Also look for clickable elements with job-related text
    clickable_selectors = [
        "div[onclick]",
        "div[role='button']",
        ".clickable",
        ".job-item",
        ".position",
    ]
    
    all_selectors = job_listing_selectors + clickable_selectors
    
    # Clean the job title for better matching
    clean_title = job_title.lower().strip()
    # Remove common words that might cause confusion
    title_keywords = [word for word in clean_title.split() if word not in ['the', 'a', 'an', 'at', 'in', 'on', 'for', 'with']]
    
    print(f"🔍 Key words to match: {title_keywords}")
    
    for selector in all_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            print(f"📊 Found {len(elements)} elements with selector: {selector}")
            
            for element in elements:
                try:
                    # Get the text content of the element
                    element_text = element.text.strip().lower()
                    # Also check href for additional context
                    href = element.get_attribute('href') or ''
                    href_text = href.lower()
                    
                    # Combine text sources for matching
                    combined_text = f"{element_text} {href_text}"
                    
                    # Check if this element contains the job title or key keywords
                    matches = 0
                    for keyword in title_keywords:
                        if keyword in combined_text:
                            matches += 1
                    
                    # Consider it a match if most keywords are found
                    match_ratio = matches / len(title_keywords) if title_keywords else 0
                    
                    if match_ratio >= 0.6:  # At least 60% of keywords match
                        print(f"🎯 Found potential match (ratio: {match_ratio:.2f}): '{element_text[:50]}...'")
                        
                        # Try to click the element
                        try:
                            # Scroll element into view
                            driver.execute_script("arguments[0].scrollIntoView(true);", element)
                            time.sleep(0.5)
                            
                            # Try clicking
                            element.click()
                            print(f"✅ Successfully clicked matching job: '{element_text[:50]}...'")
                            time.sleep(2)
                            return True
                            
                        except Exception as click_error:
                            # Try JavaScript click as fallback
                            try:
                                driver.execute_script("arguments[0].click();", element)
                                print(f"✅ Successfully clicked matching job (JS): '{element_text[:50]}...'")
                                time.sleep(2)
                                return True
                            except Exception as js_error:
                                print(f"⚠️ Could not click element: {str(click_error)}")
                                continue
                
                except Exception as e:
                    continue
                    
        except Exception as e:
            print(f"⚠️ Error with selector {selector}: {str(e)}")
            continue
    
    print("🔍 No matching job found on this page")
    return False

def upload_cv(driver):
    """Finds a file input and uploads the CV resume PDF."""
    if not os.path.exists(RESUME_FILE_PATH):
        print(f"🟡 Resume PDF file not found at {RESUME_FILE_PATH}, skipping upload.")
        return False
    
    try:
        # Look for file input fields
        file_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
        
        if not file_inputs:
            print("🟡 No CV upload field found on this page.")
            return False
            
        for file_input in file_inputs:
            try:
                # Check if this is likely a CV/resume upload field
                field_context = ""
                
                # Get surrounding text/labels
                try:
                    parent = file_input.find_element(By.XPATH, "..")
                    field_context = parent.text.lower()
                except:
                    pass
                
                # Check for CV/resume related keywords
                cv_keywords = ["cv", "resume", "curriculum", "vitae", "upload", "attach"]
                if any(keyword in field_context for keyword in cv_keywords) or not field_context:
                    print(f"✅ Found CV upload field. Context: '{field_context[:50]}...'")
                    file_input.send_keys(RESUME_FILE_PATH)
                    print("✅ Resume PDF file uploaded successfully.")
                    time.sleep(2)
                    return True
            except Exception as e:
                print(f"⚠️ Error uploading to file input: {str(e)}")
                continue
        
        print("🟡 File upload fields found but none seem to be for CV/resume.")
        return False
            
    except Exception as e:
        print(f"❌ Error during CV upload: {str(e)}")
        return False

def click_next_or_submit_button(driver):
    """Finds and clicks 'next', 'continue', or 'submit' buttons."""
    if DRY_RUN:
        print("🧪 DRY RUN: Would click submit/next button, but skipping")
        # Even in dry run, let's check for validation errors
        detect_page_errors(driver)
        return False  # Don't proceed to next page in dry run
    
    selectors = [
        "button[type='submit']",
        "input[type='submit']",
        "button:contains('Continue')",
        "button:contains('Next')",
        "button:contains('Siguiente')",
        "button:contains('Continuar')",
        "button:contains('Submit')",
        "button:contains('Enviar')",
        "button[aria-label*='next']", 
        "button[aria-label*='continue']",
        "button[id*='submit']", 
        "button[id*='next']",
        "button[class*='submit']",
        "button[class*='next']",
        "button[class*='continue']"
    ]
    
    for selector in selectors:
        try:
            if "contains" in selector:
                # Use XPath for text-based selectors
                text_part = selector.split("'")[1]
                xpath_selector = f"//button[contains(text(), '{text_part}')]"
                button = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.XPATH, xpath_selector))
                )
            else:
                button = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
            
            button_text = button.text.strip()
            print(f"✅ Found and clicking '{button_text}' button...")
            
            # Try multiple click methods
            try:
                button.click()
            except:
                driver.execute_script("arguments[0].click();", button)
            
            time.sleep(3)  # Wait for page to process/load
            
            # Check for errors after submission attempt
            if detect_page_errors(driver):
                print("❌ Form submission failed due to validation errors")
                return False
            else:
                print("✅ Form submitted successfully or moved to next page")
                return True
            
        except TimeoutException:
            continue
        except Exception as e:
            print(f"⚠️ Error with button selector {selector}: {str(e)}")
            continue
    
    print("🟡 No 'next', 'continue', or 'submit' button found on this page.")
    
    # Before giving up, check if this might be a newsletter page with an Apply button
    print("🔍 Checking for Apply buttons that might lead to the actual application...")
    apply_selectors = [
        # English buttons
        "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
        "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'start')]",
        "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'begin')]",
        "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
        "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'start')]",
        # Spanish buttons
        "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'enviar')]",
        "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'candidatura')]",
        "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'aplicar')]",
        "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'postular')]",
        "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'enviar')]",
        "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'candidatura')]",
        "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'aplicar')]"
    ]
    
    for selector in apply_selectors:
        try:
            buttons = driver.find_elements(By.XPATH, selector)
            for button in buttons:
                if button.is_displayed():
                    button_text = button.text.strip()
                    if button_text and len(button_text) < 50:
                        print(f"🎯 Found Apply button: '{button_text}' - clicking to continue to application")
                        try:
                            button.click()
                            time.sleep(3)
                            return True
            except:
                try:
                    driver.execute_script("arguments[0].click();", button)
                                time.sleep(3)
                                return True
                except:
                                continue
        except:
            continue
    
    print("❌ No Apply buttons found either - ending application process")
    return False

def detect_page_errors(driver):
    """Detect and print any validation errors on the page"""
    try:
        print("🔍 Scanning page for validation errors...")
        
        error_selectors = [
            ".error",
            ".validation-error", 
            ".field-error",
            "[role='alert']",
            ".alert-danger",
            ".text-danger",
            ".error-message",
            "[class*='error']",
            "[class*='invalid']",
            ".help-block.error",
            ".form-error",
            ".input-error",
            "[data-error]",
            ".field-validation-error"
        ]
        
        errors_found = []
        
        for selector in error_selectors:
            try:
                error_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for error in error_elements:
                    if error.is_displayed() and error.text.strip():
                        error_text = error.text.strip()
                        if error_text not in errors_found:  # Avoid duplicates
                            errors_found.append(error_text)
                    except:
                continue
        
        # Also check for fields marked as invalid
        try:
            invalid_fields = driver.find_elements(By.CSS_SELECTOR, "[aria-invalid='true']")
            for field in invalid_fields:
                field_name = field.get_attribute("name") or field.get_attribute("id") or "unknown field"
                error_msg = f"Field '{field_name}' is marked as invalid"
                if error_msg not in errors_found:
                    errors_found.append(error_msg)
        except:
            pass
        
        if errors_found:
            print(f"🚨 Found {len(errors_found)} validation errors:")
            for i, error in enumerate(errors_found, 1):
                print(f"   {i}. {error}")
            return True
        else:
            print("✅ No validation errors detected on page")
            return False
            
        except Exception as e:
        print(f"⚠️ Error detecting page errors: {str(e)}")
    return False


def dry_run_preview(driver, filled_forms):
    """Show a preview of filled forms and wait for manual review"""
    if not DRY_RUN:
        return
        
    print("\n" + "="*60)
    print("🧪 DRY RUN MODE - FORM PREVIEW")
    print("="*60)
    print(f"📋 Total fields filled: {len(filled_forms)}")
    print("\nFilled form data:")
    
    for i, form in enumerate(filled_forms, 1):
        print(f"{i}. {form['type'].upper()}: {form['label']}")
        print(f"   Value: {form['value']}")
        print()
    
    print("⏱️ Waiting 20 seconds for manual review...")
    print("💡 You can manually check the form is filled correctly")
    print("🔍 The application will NOT be submitted in dry run mode")
    print("="*60)
    
    # Wait 20 seconds
    for i in range(20, 0, -1):
        print(f"\r⏰ Time remaining: {i} seconds", end="", flush=True)
        time.sleep(1)
    
    print("\n✅ Dry run preview completed!")
    print("="*60)

def scroll_to_bottom(driver):
    """Scroll to bottom of page smoothly"""
    try:
        last_height = driver.execute_script("return document.body.scrollHeight")
        while True:
            # Scroll down smoothly
            driver.execute_script(
                "window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});"
            )
            time.sleep(1.5)
            
            # Calculate new scroll height
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height
    except Exception as e:
        print(f"⚠️ Error scrolling: {str(e)}")

def update_form_database(new_fields):
    """Update form database with new fields"""
    db_path = os.path.join(SCRIPT_DIR, "form_fields_db.json")
    
    try:
        # Load existing database
        if os.path.exists(db_path):
            with open(db_path, 'r', encoding='utf-8') as f:
                form_db = json.load(f)
        else:
            form_db = {
                "text_inputs": {},
                "selects": {},
                "textareas": {},
                "checkboxes": {},
                "radio_buttons": {},
                "file_uploads": {}
            }
        
        # Update with new fields
        for field in new_fields:
            field_type = field["type"]
            field_label = field["label"].lower()
            
            # Determine category
            if field_type == "text":
                category = "text_inputs"
            elif field_type == "select":
                category = "selects"
            elif field_type == "textarea":
                category = "textareas"
            elif field_type == "checkbox":
                category = "checkboxes"
            elif field_type == "radio":
                category = "radio_buttons"
            elif field_type == "file":
                category = "file_uploads"
            else:
                continue
            
            # Add new field if not exists
            if field_label not in form_db[category]:
                form_db[category][field_label] = {
                    "value": "",
                    "variations": [field_label],
                    "language": "auto",
                    "last_seen": str(datetime.now())
                }
            else:
                # Update variations if new
                if field_label not in form_db[category][field_label]["variations"]:
                    form_db[category][field_label]["variations"].append(field_label)
                form_db[category][field_label]["last_seen"] = str(datetime.now())
        
        # Save updated database
        with open(db_path, 'w', encoding='utf-8') as f:
            json.dump(form_db, f, indent=2, ensure_ascii=False)
            
    except Exception as e:
        print(f"❌ Error updating form database: {str(e)}")

def find_and_click_additional_apply(driver):
    """Find and click additional apply buttons on external sites"""
    apply_button_selectors = [
        # XPath selectors for text matching
        "//a[contains(text(), 'Apply')]",
        "//a[contains(text(), 'Inscríbete')]",
        "//a[contains(text(), 'Inscribirse')]",
        "//a[contains(text(), 'Postular')]",
        "//a[contains(text(), 'Enviar')]",
        "//button[contains(text(), 'Apply')]",
        "//button[contains(text(), 'Submit')]",
        "//button[contains(text(), 'Continue')]",
        
        # CSS selectors for class and ID matching
        "a.apply-job__button",
        "a.apply-job__button--manually",
        "a#buttons-social-buttons-legacy",
        "a[id*='apply']",
        "a[href*='/cv/job/']",
        "a[href*='apply_manually']",
        
        # Additional language variations with href patterns
        "a[href*='inscribirse']",
        "a[href*='postular']",
        "a[href*='apply']",
        
        # Common application button classes
        ".inca_gtm_pagina_oferta_inscribirse_manual",
        ".apply-button",
        ".application-link"
    ]
    
    original_window = driver.current_window_handle
    
    for selector in apply_button_selectors:
        try:
            # Scroll to bottom first to load all elements
            scroll_to_bottom(driver)
            time.sleep(1)
            
            by_type = By.XPATH if selector.startswith("//") else By.CSS_SELECTOR
            
            button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((by_type, selector))
            )
            
            button_text = button.text.strip()
            button_href = button.get_attribute('href') or ''
            print(f"🔍 Found additional apply button: {button_text} ({button_href})")
            
            # Click the button
            try:
                button.click()
            except:
                try:
                    driver.execute_script("arguments[0].click();", button)
                except:
                    try:
                        actions = ActionChains(driver)
                        actions.move_to_element(button)
                        actions.click()
                        actions.perform()
                    except Exception as e:
                        print(f"⚠️ Failed to click button: {str(e)}")
                        continue
            
            time.sleep(2)
            
            # Handle new window/tab
            new_window = None
            if len(driver.window_handles) > 1:
                for window_handle in driver.window_handles:
                    if window_handle != original_window:
                        new_window = window_handle
                        driver.switch_to.window(new_window)
                        break
            
            # Wait for page load and check for forms
            time.sleep(3)
            
            # Add Spanish form field selectors
            spanish_input_selectors = [
                "input[type='text']",
                "input[type='email']",
                "input[name*='email']",
                "input[id*='email']",
                "input[name*='nombre']",
                "input[id*='nombre']",
                "input[name*='telefono']",
                "input[id*='telefono']",
                "input[name*='cv']",
                "input[id*='cv']",
                # Add Talentclue specific selectors
                "#edit-field-cv-email-und-0-email",
                "#edit-field-cv-name-und-0-value",
                ".mui--is-empty",
                ".form-text"
            ]
            
            # Check if any form fields are present
            forms_present = False
            for selector in spanish_input_selectors:
                try:
                    fields = driver.find_elements(By.CSS_SELECTOR, selector)
                    if fields:
                        forms_present = True
                        break
                except:
                    continue
            
            if forms_present:
                print("✅ Found application form fields")
                return True
            else:
                print("⚠️ No form fields found on new page")
                if new_window:
                    driver.close()
                    driver.switch_to.window(original_window)
                    
        except Exception as e:
            print(f"⚠️ Error with button {selector}: {str(e)}")
            if len(driver.window_handles) > 1:
                driver.switch_to.window(original_window)
            continue
    
    print("❌ No additional apply button found")
    return False

def is_external_newsletter_form(driver):
    """
    Detect if the current external page has only a newsletter/notification form
    that should be skipped to continue to the actual application.
    
    Returns True if this appears to be a newsletter/notification form that should be skipped.
    """
    try:
        # Find all visible input fields on the page
        input_fields = driver.find_elements(By.CSS_SELECTOR, 
            "input[type='text'], input[type='email'], input[type='tel'], input[type='number'], input[type='url'], textarea, select"
        )
        
        # Filter to only visible fields
        visible_fields = []
        field_contexts = []
        
        for field in input_fields:
            if field.is_displayed():
                # Get context for this field
                context = get_field_context(driver, field).lower()
                visible_fields.append(field)
                field_contexts.append(context)
        
        print(f"🔍 Found {len(visible_fields)} visible form fields with contexts: {field_contexts}")
        
        # Check for search/filter forms or newsletter forms that should be skipped
        if len(visible_fields) <= 3:  # Include forms with up to 3 fields that might be search forms
            all_contexts = " ".join(field_contexts).lower()
            
            # Search/filter form indicators (like job search, location search)
            search_indicators = [
                'search', 'búsqueda', 'buscar', 'location', 'ubicación', 'keyword', 
                'palabra clave', 'filter', 'filtro', 'frequency', 'frecuencia'
            ]
            
            # Newsletter/notification indicators
            newsletter_indicators = [
                'email', 'newsletter', 'notification', 'updates', 'alerts', 
                'subscribe', 'marketing', 'promotional', 'comunicaciones',
                'boletín', 'correo', 'notificaciones', 'news', 'info'
            ]
            
            # Check if this looks like a search/filter form or newsletter
            is_search_form = any(indicator in all_contexts for indicator in search_indicators)
            is_newsletter_form = any(indicator in all_contexts for indicator in newsletter_indicators)
            
            if is_search_form or is_newsletter_form:
                form_type = "search/filter" if is_search_form else "newsletter/notification"
                print(f"🚫 Detected {form_type} form with {len(visible_fields)} fields: {field_contexts}")
                
                # Look for Apply/Continue buttons that suggest this isn't the main form
                apply_buttons = driver.find_elements(By.XPATH, 
                    "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')] | "
                    "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'start')] | "
                    "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'begin')] | "
                    "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')] | "
                    "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next')] | "
                    "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'enviar')] | "
                    "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'candidatura')] | "
                    "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'aplicar')] | "
                    "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continuar')] | "
                    "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')] | "
                    "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'enviar')] | "
                    "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'candidatura')] | "
                    "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'start')] | "
                    "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]"
                )
                
                if apply_buttons:
                    for btn in apply_buttons:
                        btn_text = btn.text.strip()
                        if btn_text and len(btn_text) < 100:  # Reasonable button text length
                            print(f"🔍 Found '{btn_text}' button - this suggests we need to skip this form and continue")
                            return True
                
                print(f"⚠️ {form_type.title()} form detected but no clear apply button - might need skipping")
                return True  # Better to skip than get stuck
        
        # If multiple fields, it's likely a real application form
        elif len(visible_fields) > 1:
            print("✅ Multiple form fields detected - this appears to be a real application form")
            return False
        
        # If no fields, something's wrong but don't skip
        else:
            print("⚠️ No visible form fields detected")
            return False
            
    except Exception as e:
        print(f"⚠️ Error analyzing external form type: {str(e)}")
        return False

def skip_external_newsletter_form(driver):
    """
    Skip newsletter/notification form by clicking Apply/Continue button
    Returns True if successfully skipped, False otherwise
    """
    try:
        print("⏭️ Attempting to skip newsletter/notification form...")
        
        # Look for Apply/Continue buttons but avoid social media buttons
        button_selectors = [
            # English buttons
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'start')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'begin')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next')]",
            "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
            "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'start')]",
            "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
            # Spanish buttons
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'enviar')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'candidatura')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'aplicar')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continuar')]",
            "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'enviar')]",
            "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'candidatura')]",
            "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'aplicar')]"
        ]
        
        # Social media buttons to avoid
        social_media_terms = ['facebook', 'twitter', 'linkedin', 'instagram', 'youtube', 'whatsapp', 'telegram', 'share', 'follow', 'fb', 'ig', 'tw']
        
        # Additional social media patterns to check
        social_media_patterns = [
            'facebook.com', 'twitter.com', 'linkedin.com', 'instagram.com', 
            'youtube.com', 'whatsapp.com', 'telegram.org', 'fb.com',
            'social-media', 'social_media', 'sharebutton', 'share-button'
        ]
        
        for selector in button_selectors:
            try:
                buttons = driver.find_elements(By.XPATH, selector)
                for button in buttons:
                    if button.is_displayed():
                        button_text = button.text.strip()
                        button_text_lower = button_text.lower()
                        
                        # Skip social media buttons
                        if any(social_term in button_text_lower for social_term in social_media_terms):
                            print(f"⏭️ Skipping social media button: '{button_text}'")
                            continue
                        
                        # Skip buttons with URLs that look like social media
                        try:
                            button_href = button.get_attribute('href') or ''
                            button_class = button.get_attribute('class') or ''
                            button_id = button.get_attribute('id') or ''
                            
                            # Check href for social media patterns
                            if any(social_term in button_href.lower() for social_term in social_media_terms):
                                print(f"⏭️ Skipping social media link: '{button_text}' (href: {button_href})")
                                continue
                            
                            # Check for social media patterns in href
                            if any(pattern in button_href.lower() for pattern in social_media_patterns):
                                print(f"⏭️ Skipping social media link: '{button_text}' (href contains: {button_href})")
                                continue
                            
                            # Check class and id for social media indicators
                            if any(social_term in button_class.lower() for social_term in social_media_terms):
                                print(f"⏭️ Skipping social media button by class: '{button_text}' (class: {button_class})")
                                continue
                                
                            if any(social_term in button_id.lower() for social_term in social_media_terms):
                                print(f"⏭️ Skipping social media button by id: '{button_text}' (id: {button_id})")
                                continue
                        except Exception as e:
                            print(f"⚠️ Error checking social media patterns: {str(e)}")
                            pass
                        
                        if button_text and len(button_text) < 50:  # Reasonable button text
                            print(f"✅ Clicking '{button_text}' to skip newsletter form")
                            
                            # Try different click methods
                            try:
                                button.click()
                            except:
                                try:
                                    driver.execute_script("arguments[0].click();", button)
                                except:
                                    ActionChains(driver).move_to_element(button).click().perform()
                            
                            time.sleep(2)  # Wait for any dropdown/submenu to appear
                            
                            # Check if a submenu appeared with additional options
                            submenu_selectors = [
                                "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'solicitud')]",
                                "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'enviar')]",
                                "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'solicitud')]",
                                "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'enviar')]",
                                "//li[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'solicitud')]//a",
                                "//li[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'enviar')]//a"
                            ]
                            
                            submenu_clicked = False
                            for submenu_selector in submenu_selectors:
                                try:
                                    submenu_items = driver.find_elements(By.XPATH, submenu_selector)
                                    for submenu_item in submenu_items:
                                        if submenu_item.is_displayed():
                                            submenu_text = submenu_item.text.strip()
                                            if submenu_text and len(submenu_text) < 100:
                                                print(f"🔽 Found submenu option: '{submenu_text}' - clicking to proceed")
                                                try:
                                                    submenu_item.click()
                                                except:
                                                    try:
                                                        driver.execute_script("arguments[0].click();", submenu_item)
                                                    except:
                                                        ActionChains(driver).move_to_element(submenu_item).click().perform()
                                                submenu_clicked = True
                                                break
                                    if submenu_clicked:
                                        break
                                except:
                                    continue
                            
                            time.sleep(3)  # Wait for page to load after submenu click
                            return True
            except:
                continue
        
        print("❌ Could not find button to skip newsletter form")
        return False
        
    except Exception as e:
        print(f"⚠️ Error skipping newsletter form: {str(e)}")
        return False

def analyze_and_fill_form(driver, form_data, job_analysis=None, job_title=""):
    """Analyze form fields and attempt to fill with comprehensive data"""
    # Wait for page to fully load
    print("⏳ Waiting for form to load...")
    time.sleep(3)
    scroll_to_bottom(driver)
    time.sleep(2)
    
    # Debug: Print page title and check if we're on the right page
    try:
        page_title = driver.title
        current_url = driver.current_url
        print(f"📄 Page title: {page_title}")
        print(f"🔗 Current URL: {current_url}")
    except:
        pass
    
    # Handle cookies popup before any form analysis
    handle_cookies_popup(driver)
    time.sleep(1)
    
    # Check if this is a newsletter form that should be skipped
    # BUT first check if this might be a job listing page that we need to navigate
    page_source_lower = driver.page_source.lower()
    
    # Don't run newsletter detection if we're on a job listings page
    job_listing_indicators = [
        'job-title', 'job-card', 'job-listing', 'position', 'career', 'opening',
        'apply now', 'job board', 'vacancies', 'opportunities', 'empleos',
        'current vacancies', 'job openings', 'read more'
    ]
    
    is_job_listing_page = any(indicator in page_source_lower for indicator in job_listing_indicators)
    
    # First, check if there's already an Apply button on this page (single job page)
    apply_buttons = driver.find_elements(By.CSS_SELECTOR, 
        "button[class*='apply'], a[class*='apply'], button[class*='submit'], a[class*='submit']"
    )
    
    # Also check with XPath for apply buttons
    apply_buttons_xpath = driver.find_elements(By.XPATH, 
        "//button[contains(text(), 'Apply')] | //a[contains(text(), 'Apply')] | "
        "//button[contains(text(), 'Submit')] | //a[contains(text(), 'Submit')] | "
        "//button[contains(text(), 'Aplicar')] | //a[contains(text(), 'Aplicar')]"
    )
    apply_buttons.extend(apply_buttons_xpath)
    
    # Filter out save buttons and prioritize real apply buttons
    filtered_apply_buttons = []
    for btn in apply_buttons:
        try:
            btn_text = btn.text.lower().strip()
            btn_class = btn.get_attribute('class') or ''
            btn_id = btn.get_attribute('id') or ''
            
            # Skip save buttons
            if any(save_term in btn_text for save_term in ['save', 'saved', 'guardar', 'guardado']):
                print(f"⏭️ Skipping save button: '{btn.text}'")
                continue
            
            # Skip if class or id suggests it's a save button
            if any(save_term in btn_class.lower() for save_term in ['save', 'bookmark']):
                print(f"⏭️ Skipping save button by class: '{btn.text}' (class: {btn_class})")
                continue
                
            if any(save_term in btn_id.lower() for save_term in ['save', 'bookmark']):
                print(f"⏭️ Skipping save button by id: '{btn.text}' (id: {btn_id})")
                continue
            
            # Prioritize buttons with apply/submit terms
            if any(apply_term in btn_text for apply_term in ['apply', 'submit', 'aplicar', 'enviar']):
                filtered_apply_buttons.append(btn)
                
        except Exception as e:
            print(f"⚠️ Error filtering button: {str(e)}")
            continue
    
    apply_buttons = filtered_apply_buttons
    
    if apply_buttons:
        print(f"🎯 Found {len(apply_buttons)} apply buttons on single job page")
        for btn in apply_buttons:
            if btn.is_displayed():
                print(f"✅ Found apply button: {btn.text}")
                try:
                    btn.click()
                    print("✅ Clicked apply button - proceeding to application form")
                    time.sleep(2)
                    # Continue to form filling after clicking apply
                    break
                except:
                    try:
                        driver.execute_script("arguments[0].click();", btn)
                        print("✅ JavaScript clicked apply button - proceeding to application form")
                        time.sleep(2)
                        break
                    except:
                        continue
    
    # Check if this is a job board with multiple job listings that we need to navigate
    elif is_job_listing_page:
        print("🔍 Detected job listing page - checking for job matching before form filling")
        
        # Try to find and click the matching job based on the LinkedIn job title
        # Get the job title from the current context (this should be passed from the caller)
        try:
            # Look for job titles or "Read More" buttons that might lead to specific jobs
            job_links = driver.find_elements(By.CSS_SELECTOR, 
                "a[href*='job'], a, button, h1, h2, h3, h4, h5, h6, .job-title, .position-title, [class*='job'], [class*='position'], [class*='font-bold']"
            )
            
            # Also try XPath for "Read More" text and specific job title patterns
            read_more_links = driver.find_elements(By.XPATH, "//a[contains(text(), 'Read More')] | //button[contains(text(), 'Read More')]")
            job_links.extend(read_more_links)
            
            # Try specific patterns for job titles
            job_title_elements = driver.find_elements(By.XPATH, 
                "//h1[contains(text(), 'Data')] | //h2[contains(text(), 'Data')] | //h3[contains(text(), 'Data')] | "
                "//h4[contains(text(), 'Data')] | //h5[contains(text(), 'Data')] | //h6[contains(text(), 'Data')] | "
                "//h1[contains(text(), 'Analyst')] | //h2[contains(text(), 'Analyst')] | //h3[contains(text(), 'Analyst')] | "
                "//h4[contains(text(), 'Analyst')] | //h5[contains(text(), 'Analyst')] | //h6[contains(text(), 'Analyst')] | "
                "//h1[contains(text(), 'Analytics')] | //h2[contains(text(), 'Analytics')] | //h3[contains(text(), 'Analytics')] | "
                "//h4[contains(text(), 'Analytics')] | //h5[contains(text(), 'Analytics')] | //h6[contains(text(), 'Analytics')]"
            )
            job_links.extend(job_title_elements)
            
            print(f"🔍 Found {len(job_links)} potential job links/titles on listing page")
            
            # Debug: Print first few elements found
            print("🔍 First few elements found:")
            for i, link in enumerate(job_links[:10]):  # Show first 10
                try:
                    print(f"   {i+1}. Tag: {link.tag_name}, Text: '{link.text.strip()[:50]}', Classes: {link.get_attribute('class')}")
                except:
                    print(f"   {i+1}. (Error getting element info)")
            
            # Try to match job titles with the LinkedIn job title or common data science terms
            search_terms = []
            if job_title:
                # Extract key terms from the LinkedIn job title
                job_title_lower = job_title.lower()
                search_terms.extend([
                    job_title_lower,
                    job_title_lower.split()[0] if job_title_lower.split() else "",  # First word
                    job_title_lower.split()[-1] if job_title_lower.split() else ""   # Last word
                ])
            
            # Add common data science terms as fallback
            search_terms.extend([
                'data scientist', 'data analyst', 'analytics', 'machine learning', 
                'ml engineer', 'ai engineer', 'business intelligence', 'bi analyst',
                'game data analyst', 'principal data analyst', 'senior data'
            ])
            
            print(f"🔍 Searching for job titles matching: {search_terms}")
            
            for link in job_links:
                try:
                    link_text = link.text.lower().strip()
                    if link_text and any(term in link_text for term in search_terms if term):
                        print(f"🎯 Found matching job: {link.text}")
                        
                        # Scroll to element and click
                        driver.execute_script("arguments[0].scrollIntoView(true);", link)
                        time.sleep(1)
                        
                        # Try clicking the element or its clickable parent
                        clicked_successfully = False
                        
                        # First try clicking the element itself
                        try:
                            link.click()
                            print(f"✅ Clicked on job: {link.text}")
                            time.sleep(3)  # Wait for page to load
                            clicked_successfully = True
                        except:
                            # If element is not clickable, try its parent elements
                            try:
                                parent = link.find_element(By.XPATH, "..")
                                parent.click()
                                print(f"✅ Clicked on job parent: {link.text}")
                                time.sleep(3)
                                clicked_successfully = True
                            except:
                                # Try JavaScript click on the original element
                                try:
                                    driver.execute_script("arguments[0].click();", link)
                                    print(f"✅ JavaScript clicked on job: {link.text}")
                                    time.sleep(3)
                                    clicked_successfully = True
                                except:
                                    # Try JavaScript click on parent
                                    try:
                                        parent = link.find_element(By.XPATH, "..")
                                        driver.execute_script("arguments[0].click();", parent)
                                        print(f"✅ JavaScript clicked on job parent: {link.text}")
                                        time.sleep(3)
                                        clicked_successfully = True
                                    except:
                                        print(f"⚠️ Could not click job link: {link.text}")
                                        continue
                        
                        if clicked_successfully:
                            # Check if we're now on a job details page
                            if 'job' in driver.current_url.lower() or 'position' in driver.current_url.lower():
                                print("✅ Successfully navigated to job details page")
                                return ["job_navigation_successful"]
                            else:
                                print("🔍 Still on listing page, continuing to form filling")
                                break
                                
                except Exception as e:
                    print(f"⚠️ Error clicking job link: {str(e)}")
                    continue
                    
        except Exception as e:
            print(f"⚠️ Error during job matching: {str(e)}")
        
        print("🔍 No specific job match found, proceeding with form filling")
    
    if not is_job_listing_page and is_external_newsletter_form(driver):
        if skip_external_newsletter_form(driver):
            print("✅ Successfully skipped newsletter form, continuing to next page")
            return ["newsletter_skipped"]  # Return special marker to indicate we skipped this page
        else:
            print("⚠️ Newsletter form detected but couldn't skip - this might cause issues")
            # Don't fill the newsletter form, just return empty to avoid getting stuck
            return ["newsletter_detected_but_not_skipped"]
    
    forms_found = []
    
    field_mappings = {
        "first_name": {
            "keywords": ["first name", "firstname", "first_name", "nombre", "given name"],
            "value": "Jessie Lee"  # First name is "Jessie Lee"
        },
        "last_name": {
            "keywords": ["last name", "lastname", "last_name", "apellido", "surname", "family name"],
            "value": "Delgadillo Newman"  # Last name is "Delgadillo Newman"
        },
        "name": {
            "keywords": ["name", "full name", "full_name"],
            "value": form_data["personal"]["name"]
        },
        "email": {
            "keywords": ["email", "correo", "e-mail", "full_email"],
            "value": form_data["personal"]["email"]
        },
        "phone": {
            "keywords": ["phone", "telephone", "mobile", "teléfono", "tel"],
            "value": get_appropriate_phone_number(driver, form_data)
        },
        "linkedin": {
            "keywords": ["linkedin", "linkedin_url", "linkedin_profile", "linkedin_uid"],
            "value": form_data["personal"]["linkedin"]
        },
        "website": {
            "keywords": ["website", "site", "portfolio"],
            "value": form_data["personal"]["website"]
        },
        "experience": {
            "keywords": ["experience", "years", "años", "experience_years"],
            "value": form_data["professional"]["years_experience"]
        },
        "salary": {
            "keywords": ["salary", "salario", "expected", "desired", "compensation", "requirements"],
            "value": get_salary_for_job(form_data, job_analysis)
        },
        "country": {
            "keywords": ["country", "país", "nation", "nationality", "location", "region", "región", "where", "ciudadanía"],
            "value": get_country_from_cv()
        }
    }
    
    # Enhanced field detection and filling
    print("🔍 Analyzing form fields...")
    
    # 1. Handle text input fields
    input_fields = driver.find_elements(By.CSS_SELECTOR, 
        "input[type='text'], input[type='email'], input[type='tel'], input[type='number'], input[type='url'], textarea"
    )
    
    for field in input_fields:
        try:
            # Get field context (label, placeholder, etc.)
            field_context = get_field_context(driver, field)
            field_label = field_context.lower()
            
            # Skip hidden fields
            if not field.is_displayed():
                continue
                
            print(f"🔍 Analyzing field: {field_context}")
            
            # Try to match field with known types
            filled = False
            for field_type, mapping in field_mappings.items():
                if any(keyword in field_label for keyword in mapping["keywords"]):
                    print(f"📝 Found {field_type} field: {field_context}")
                    
                    success = fill_text_field(driver, field, mapping["value"])
                    
                    # Special handling for phone numbers - only for main phone field, not extension
                    if field_type == "phone" and success and "extension" not in field_label:
                        time.sleep(1)  # Wait for validation
                        if has_validation_errors(driver, field):
                            print("📞 Phone field has validation error, trying alternative formats...")
                            phone_value = mapping["value"]
                            success = try_multiple_phone_formats(driver, field, phone_value)
                            if success:
                                print("✅ Phone number filled with alternative format")
                            else:
                                print("❌ All phone formats failed validation")
                    elif not success and field_type == "phone" and "extension" not in field_label:
                        phone_value = mapping["value"]
                        success = try_multiple_phone_formats(driver, field, phone_value)
                    
                    if success:
                        forms_found.append({
                            "type": field_type,
                            "label": field_context,
                            "value": mapping["value"]
                        })
                        filled = True
                        break
            
            # If not matched, try keyword-based answer
            if not filled:
                answer = find_keyword_answer(field_context, form_data)
                if answer:
                    success = fill_text_field(driver, field, answer)
                    if success:
                        forms_found.append({
                            "type": "keyword_match",
                            "label": field_context,
                            "value": answer
                        })
                        
        except Exception as e:
            print(f"⚠️ Error analyzing text field: {str(e)}")
            continue
    
    # 2. Collect all radio button and dropdown questions for LLM analysis
    questions_data = []
    
    # Collect radio button questions
    radio_groups = find_radio_groups(driver)
    radio_group_mapping = {}  # Map question to radio elements
    
    for group_name, radio_buttons in radio_groups.items():
        try:
            print(f"🔘 Found radio group: {group_name}")
            
            # Get all options for this group and extract the question text
            options = []
            element_mapping = {}
            question_text = None
            
            for radio in radio_buttons:
                try:
                    radio_id = radio.get_attribute("id")
                    label_text = get_radio_label_text(driver, radio, radio_id)
                    options.append(label_text)
                    element_mapping[label_text] = radio
                    
                    # Try to extract the actual question text from the first radio button
                    if question_text is None:
                        question_text = extract_question_text_from_radio(driver, radio)
                        
                        except Exception as e:
                    print(f"⚠️ Error processing radio option: {str(e)}")
                            continue
                    
            if options:
                # Use extracted question text or fall back to group name
                display_question = question_text if question_text else group_name
                print(f"   Question: {display_question}")
                
                questions_data.append({
                    "question": display_question,
                    "options": options,
                    "question_type": "radio"
                })
                radio_group_mapping[display_question] = element_mapping
                print(f"   Added {len(options)} options to LLM analysis")
                
        except Exception as e:
            print(f"⚠️ Error collecting radio group {group_name}: {str(e)}")
    
    # Collect custom radio questions  
    custom_radio_groups = find_custom_radio_elements(driver)
    custom_group_mapping = {}
    
    for group_name, elements in custom_radio_groups.items():
        try:
            print(f"🔘 Found custom radio group: {group_name}")
            
            options = []
            element_mapping = {}
            for element in elements:
                try:
                    element_text = element.text.strip()
                    if element_text and len(element_text) < 200:  # Skip very long texts
                        options.append(element_text)
                        element_mapping[element_text] = element
                except Exception as e:
                    print(f"⚠️ Error processing custom radio option: {str(e)}")
                    continue
            
            if options:
                questions_data.append({
                    "question": group_name,
                    "options": options,
                    "question_type": "custom_radio"
                })
                custom_group_mapping[group_name] = element_mapping
                print(f"   Added {len(options)} custom options to LLM analysis")
                
        except Exception as e:
            print(f"⚠️ Error collecting custom radio group {group_name}: {str(e)}")
    
    # Collect dropdown questions
    dropdowns = driver.find_elements(By.CSS_SELECTOR, "select")
    dropdown_mapping = {}
    
    for dropdown in dropdowns:
        try:
            if not dropdown.is_displayed():
                continue
                
            # Get dropdown context
            context = get_field_context(driver, dropdown)
            
            # Get options
            from selenium.webdriver.support.ui import Select
            select = Select(dropdown)
            options = [opt.text.strip() for opt in select.options if opt.text.strip()]
            
            if options and len(options) > 1:  # Skip dropdowns with no real options
                questions_data.append({
                    "question": context,
                    "options": options,
                    "question_type": "dropdown"
                })
                dropdown_mapping[context] = dropdown
                print(f"🔽 Found dropdown '{context}' with {len(options)} options")
                
        except Exception as e:
            print(f"⚠️ Error collecting dropdown: {str(e)}")
            continue
    
    # Load CV content for LLM analysis
    cv_content = ""
    try:
        cv_path = os.path.join(os.path.dirname(__file__), "cv_text.txt")
        with open(cv_path, 'r', encoding='utf-8') as f:
            cv_content = f.read()
    except Exception as e:
        print(f"⚠️ Could not load CV content: {str(e)}")
    
    # Get job description if available
    job_description = ""
    if job_analysis and job_analysis.get('description'):
        job_description = job_analysis['description'][:1000]  # First 1000 chars
    
    # Analyze all questions with LLM
    if questions_data:
        print(f"\n🧠 Analyzing {len(questions_data)} questions with LLM...")
        llm_selections = analyze_form_questions_with_llm(questions_data, cv_content, job_description)
        
        # If LLM analysis failed or returned no results, use fallback logic
        if not llm_selections:
            print("⚠️ LLM analysis failed, using fallback intelligent selection...")
            llm_selections = apply_fallback_radio_selections(questions_data, form_data)
        else:
            print(f"✅ LLM provided {len(llm_selections)} answers")
            # For any questions the LLM didn't answer, use fallback logic
            unanswered_questions = [q for q in questions_data if q['question'] not in llm_selections]
            if unanswered_questions:
                print(f"🔄 Using fallback for {len(unanswered_questions)} unanswered questions...")
                fallback_selections = apply_fallback_radio_selections(unanswered_questions, form_data)
                llm_selections.update(fallback_selections)
        
        # Apply LLM selections to radio buttons
        for question, selected_option in llm_selections.items():
            try:
                # Handle regular radio buttons
                if question in radio_group_mapping:
                    element_mapping = radio_group_mapping[question]
                    if selected_option in element_mapping:
                        radio_element = element_mapping[selected_option]
                        
                        # Try to click the radio button
                        success = False
                        if not radio_element.is_displayed():
                            success = try_click_radio_label(driver, radio_element)
                        else:
                            try:
                                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", radio_element)
                                time.sleep(0.5)
                                radio_element.click()
                                success = True
                            except:
                                try:
                                    driver.execute_script("arguments[0].click();", radio_element)
                                    success = True
                                except:
                                    success = try_click_radio_label(driver, radio_element)
                        
                        if success:
                            print(f"✅ Selected radio option: {selected_option}")
                    forms_found.append({
                                "type": "radio",
                                "label": question,
                                "value": selected_option
                            })
                        else:
                            print(f"❌ Failed to click radio option: {selected_option}")
                
                # Handle custom radio elements
                elif question in custom_group_mapping:
                    element_mapping = custom_group_mapping[question]
                    if selected_option in element_mapping:
                        custom_element = element_mapping[selected_option]
                        
                        try:
                            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", custom_element)
                            time.sleep(0.5)
                            custom_element.click()
                            print(f"✅ Selected custom radio option: {selected_option}")
                            forms_found.append({
                                "type": "custom_radio",
                                "label": question,
                                "value": selected_option
                            })
                        except Exception as e:
                            print(f"❌ Failed to click custom radio option: {selected_option} - {str(e)}")
                
                # Handle dropdowns
                elif question in dropdown_mapping:
                    dropdown_element = dropdown_mapping[question]
                    
                    try:
                        from selenium.webdriver.support.ui import Select
                        select = Select(dropdown_element)
                        
                        # Try to select by visible text
                        for option in select.options:
                            if option.text.strip() == selected_option:
                                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", dropdown_element)
                                time.sleep(0.5)
                                select.select_by_visible_text(option.text)
                                print(f"✅ Selected dropdown option: {selected_option}")
                                forms_found.append({
                                    "type": "dropdown",
                                    "label": question,
                                    "value": selected_option
                    })
                    break
                        else:
                            print(f"❌ Could not find dropdown option: {selected_option}")
            
        except Exception as e:
                        print(f"❌ Failed to select dropdown option: {selected_option} - {str(e)}")
                
                else:
                    print(f"⚠️ Could not map question to element: {question}")
                    
            except Exception as e:
                print(f"⚠️ Error applying LLM selection for '{question}': {str(e)}")
    
    else:
        print("ℹ️ No radio buttons or dropdowns found for LLM analysis")
    
    # 3. Handle checkboxes
    checkboxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
    print(f"☑️ Found {len(checkboxes)} checkboxes on page")
    
    # Group checkboxes by name (like gender identity questions)
    checkbox_groups = {}
    privacy_checkboxes = []
    
    for i, checkbox in enumerate(checkboxes):
        try:
            checkbox_name = checkbox.get_attribute("name") or ""
            checkbox_id = checkbox.get_attribute("id") or ""
            checkbox_context = get_field_context(driver, checkbox)
            
            print(f"   Checkbox {i+1}: name='{checkbox_name}', id='{checkbox_id}', context='{checkbox_context[:50]}...'")
            
            # Check if it's a privacy/terms checkbox (including by name)
            if (any(term in checkbox_context.lower() for term in ["privacy", "terms", "data", "policy", "consent", "agree", "controller", "legal"]) or 
                checkbox_name in ["new_legal_notice", "legal_notice", "privacy_policy"]):
                privacy_checkboxes.append({
                    "element": checkbox,
                    "context": checkbox_context,
                    "name": checkbox_name,
                    "id": checkbox_id
                })
            # Group other checkboxes by name (for gender identity, etc.)
            elif checkbox_name:
                if checkbox_name not in checkbox_groups:
                    checkbox_groups[checkbox_name] = []
                checkbox_groups[checkbox_name].append({
                    "element": checkbox,
                    "context": checkbox_context,
                    "id": checkbox_id
                })
                
        except Exception as e:
            print(f"⚠️ Error analyzing checkbox {i+1}: {str(e)}")
    
    # Handle privacy/terms checkboxes first
    for privacy_cb in privacy_checkboxes:
        try:
            checkbox = privacy_cb["element"]
            if not checkbox.is_selected():
                print(f"🔒 Found privacy checkbox: {privacy_cb['context'][:50]}...")
                success = False
                
                # If checkbox is visible, try direct click first
                if checkbox.is_displayed():
                    try:
                        checkbox.click()
                        print("✅ Clicked privacy checkbox directly")
                        success = True
                    except Exception as e1:
                        print(f"❌ Direct privacy click failed: {str(e1)}")
                
                # Try JavaScript click
                if not success:
                    try:
                        driver.execute_script("arguments[0].click();", checkbox)
                        print("✅ Clicked privacy checkbox via JavaScript")
                        success = True
                    except Exception as e2:
                        print(f"❌ JavaScript privacy click failed: {str(e2)}")
                
                # Try clicking associated label (works for hidden elements)
                if not success and privacy_cb["id"]:
                    try:
                        label = driver.find_element(By.CSS_SELECTOR, f"label[for='{privacy_cb['id']}']")
                        label.click()
                        print("✅ Clicked privacy checkbox via label (hidden)")
                        success = True
                    except Exception as e3:
                        print(f"❌ Privacy label click failed: {str(e3)}")
                
                if success:
                    print(f"✅ Checked privacy/terms: {privacy_cb['context'][:50]}...")
                    forms_found.append({
                        "type": "checkbox",
                        "label": privacy_cb["context"],
                        "value": "checked"
                    })
                else:
                    print(f"❌ Failed to check privacy checkbox")
                
        except Exception as e:
            print(f"⚠️ Error checking privacy checkbox: {str(e)}")
    
    # Handle grouped checkboxes (like gender identity)
    for group_name, checkboxes_in_group in checkbox_groups.items():
        try:
            print(f"☑️ Found checkbox group '{group_name}' with {len(checkboxes_in_group)} options:")
            for cb in checkboxes_in_group:
                print(f"   - {cb['context']}")
            
            # Determine if this is a gender identity group
            all_contexts = " ".join([cb["context"].lower() for cb in checkboxes_in_group])
            
            if any(word in all_contexts for word in ["gender", "woman", "man", "identity", "genderqueer", "non-binary"]):
                print("🚹🚺 Detected gender identity checkbox group")
                selected_option = handle_gender_checkbox_group(driver, checkboxes_in_group, form_data)
                if selected_option:
                    forms_found.append({
                        "type": "checkbox_group",
                        "label": f"gender_group_{group_name}",
                        "value": selected_option
                    })
                else:
                    print("⚠️ No gender option was selected")
            else:
                print(f"🔍 Unknown checkbox group type for '{group_name}'")
                
        except Exception as e:
            print(f"⚠️ Error handling checkbox group {group_name}: {str(e)}")
    
    # 4. Handle dropdowns/select fields
    select_fields = driver.find_elements(By.CSS_SELECTOR, "select")
    for select_field in select_fields:
        try:
            if not select_field.is_displayed():
            continue
                
            select_context = get_field_context(driver, select_field)
            print(f"📋 Found dropdown: {select_context}")
            
            selected = handle_dropdown(driver, select_field, select_context, form_data)
            if selected:
                forms_found.append({
                    "type": "select",
                    "label": select_context,
                    "value": selected
                })
                
        except Exception as e:
            print(f"⚠️ Error handling dropdown: {str(e)}")
    
    # 5. Handle Workday-style custom dropdowns (non-select elements)
    workday_selectors = [
        "[data-automation-id*='searchBox']",
        "[data-automation-id*='selectinput']",
        "[data-automation-id*='dropdown']",
        "[data-automation-id*='combobox']"
    ]
    
    for selector in workday_selectors:
        try:
            workday_fields = driver.find_elements(By.CSS_SELECTOR, selector)
            for field in workday_fields:
                try:
                    if not field.is_displayed():
                        continue
                        
                    field_context = get_field_context(driver, field)
                    print(f"🔧 Found Workday-style field: {field_context}")
                    
                    # Handle this as a Workday dropdown
                    selected = handle_workday_dropdown(driver, field, field_context)
                    if selected:
                        forms_found.append({
                            "type": "workday_dropdown",
                            "label": field_context,
                            "value": selected
                        })
                        
                except Exception as e:
                    print(f"⚠️ Error handling Workday field: {str(e)}")
                    
        except Exception as e:
            print(f"⚠️ Error finding Workday fields with selector {selector}: {str(e)}")
    
    # Update form database
    update_form_database(forms_found)
    
    print(f"✅ Filled {len(forms_found)} form fields")
    return forms_found


def get_field_context(driver, field):
    """Get comprehensive context for a form field"""
    context_parts = []
    
    # Get field attributes
    field_id = field.get_attribute("id") or ""
    field_name = field.get_attribute("name") or ""
    field_placeholder = field.get_attribute("placeholder") or ""
    field_type = field.get_attribute("type") or ""
    
    context_parts.extend([field_id, field_name, field_placeholder])
    
    # Try to find associated label
    try:
        if field_id:
            label = driver.find_element(By.CSS_SELECTOR, f"label[for='{field_id}']")
            context_parts.append(label.text)
    except:
        pass
    
    # Look for nearby text (parent elements)
    try:
        parent = field.find_element(By.XPATH, "..")
        parent_text = parent.text.strip()
        if parent_text and len(parent_text) < 200:  # Avoid very long texts
            context_parts.append(parent_text)
    except:
        pass
    
    # Look for preceding text
    try:
        preceding = field.find_element(By.XPATH, "./preceding-sibling::*[1]")
        if preceding.text:
            context_parts.append(preceding.text)
    except:
        pass
    
    return " ".join(filter(None, context_parts))


def fill_text_field(driver, field, value):
    """Fill a text field with multiple fallback methods"""
    try:
        # Method 1: Clear and send keys
        field.clear()
        field.send_keys(value)
        return True
    except:
        try:
            # Method 2: JavaScript setValue
            field.clear()
            field.send_keys(value)
            return True
        except:
            try:
                # Method 3: Pure JavaScript
                driver.execute_script(f"arguments[0].value = '{value}';", field)
                return True
            except Exception as e:
                print(f"⚠️ Could not fill field: {str(e)}")
                return False

def try_multiple_phone_formats(driver, field, original_phone):
    """Try multiple phone number formats until one works"""
    import re
    
    # Generate different phone format variations
    formats_to_try = []
    
    # Original phone: +34 654 808 087
    base_number = re.sub(r'[^\d]', '', original_phone)  # Extract just digits: 34654808087
    country_code = re.search(r'\+(\d{1,4})', original_phone)
    country_code = country_code.group(1) if country_code else "34"
    local_number = base_number[len(country_code):]  # Remove country code from digits
    
    # Try different formats
    formats_to_try = [
        original_phone,                           # +34 654 808 087
        f"+{country_code}{local_number}",        # +34654808087
        f"+{country_code} {local_number}",       # +34 654808087
        local_number,                            # 654808087
        f"{local_number[:3]} {local_number[3:6]} {local_number[6:]}", # 654 808 087
        f"{local_number[:3]}{local_number[3:6]}{local_number[6:]}"   # 654808087
    ]
    
    print(f"📞 Trying {len(formats_to_try)} phone formats for: {original_phone}")
    
    for i, phone_format in enumerate(formats_to_try, 1):
        print(f"📞 Format {i}/{len(formats_to_try)}: '{phone_format}'")
        
        # Clear field first
        try:
            field.clear()
            time.sleep(0.5)
        except:
            pass
        
        # Try to fill with this format
        success = fill_text_field(driver, field, phone_format)
        if success:
            # Check if there are any validation errors after filling
            time.sleep(1)  # Wait for validation
            if not has_validation_errors(driver, field):
                print(f"✅ Phone format {i} worked: '{phone_format}'")
                return True
            else:
                print(f"❌ Phone format {i} caused validation error")
    
    print("❌ All phone formats failed")
    return False

def has_validation_errors(driver, field):
    """Check if there are validation errors near the field"""
    try:
        # Look for common error indicators near the field
        error_selectors = [
            ".error",
            ".validation-error", 
            ".field-error",
            "[role='alert']",
            ".alert-danger",
            ".text-danger",
            ".error-message",
            "[class*='error']",
            "[class*='invalid']"
        ]
        
        # Check in parent containers and siblings
        containers_to_check = []
        try:
            containers_to_check.append(field.find_element(By.XPATH, ".."))  # Parent
            containers_to_check.append(field.find_element(By.XPATH, "../.."))  # Grandparent
        except:
            pass
        
        for container in containers_to_check:
            for selector in error_selectors:
                try:
                    errors = container.find_elements(By.CSS_SELECTOR, selector)
                    if errors:
                        for error in errors:
                            if error.is_displayed() and error.text.strip():
                                error_text = error.text.strip()
                                print(f"🚨 Validation error found: '{error_text}'")
                                return True
                except:
                    continue
        
        # Check for general "Enter a valid" pattern in page source
        try:
            page_source = driver.page_source
            # Generalized error detection for any validation error
            if "Enter a valid" in page_source:
                print(f"🚨 Found 'Enter a valid' validation error in page")
                return True
            
            # Also check for specific error phrases
            error_phrases = [
                "invalid phone", 
                "phone format",
                "valid phone number",
                "format is invalid",
                "not valid",
                "invalid format"
            ]
            for phrase in error_phrases:
                if phrase in page_source:
                    print(f"🚨 Found error phrase in page: '{phrase}'")
                    return True
        except:
            pass
        
        # Also check for aria-invalid attribute
        if field.get_attribute("aria-invalid") == "true":
            print(f"🚨 Field marked as aria-invalid")
            return True
            
        return False
        
    except Exception as e:
        print(f"⚠️ Error checking validation: {str(e)}")
        return False


def find_radio_groups(driver):
    """Find and group radio buttons by name with enhanced detection"""
    # Use more comprehensive selectors to find all radio buttons
    radio_selectors = [
        "input[type='radio']",
        "input[type='radio'][style*='display: none']",  # Hidden radios
        "input[type='radio'][style*='visibility: hidden']",  # Hidden radios
        "input[type='radio'][hidden]",  # Hidden attribute
    ]
    
    all_radios = []
    for selector in radio_selectors:
        try:
            radios = driver.find_elements(By.CSS_SELECTOR, selector)
            all_radios.extend(radios)
        except:
            continue
    
    # Remove duplicates while preserving order
    unique_radios = []
    seen = set()
    for radio in all_radios:
        radio_id = id(radio)
        if radio_id not in seen:
            unique_radios.append(radio)
            seen.add(radio_id)
    
    groups = {}
    
    print(f"🔍 Found {len(unique_radios)} radio buttons on page (including hidden ones)")
    
    for i, radio in enumerate(unique_radios):
        try:
            # Debug radio button attributes (process both hidden and visible)
            is_displayed = radio.is_displayed()
            is_enabled = radio.is_enabled()
            print(f"   Radio {i+1}: {'Visible' if is_displayed else 'Hidden'}, {'Enabled' if is_enabled else 'Disabled'}")
            
            # Debug radio button attributes
            radio_id = radio.get_attribute("id") or ""
            radio_name = radio.get_attribute("name") or ""
            radio_value = radio.get_attribute("value") or ""
            radio_class = radio.get_attribute("class") or ""
            
            print(f"   Radio {i+1}: id='{radio_id}', name='{radio_name}', value='{radio_value}', class='{radio_class}'")
            
            # Get grouping name (try multiple strategies)
            name = radio_name
            if not name:
                # Strategy 1: Use ID patterns
                if radio_id:
                    # Pattern: question_123_456 -> question_123
                    if radio_id.startswith("question_") and "_" in radio_id[9:]:
                        parts = radio_id.split("_")
                        if len(parts) >= 3:
                            name = f"{parts[0]}_{parts[1]}"
                    else:
                        name = radio_id
                
                # Strategy 2: Look for parent form elements with names
                if not name:
                    try:
                        parent_div = radio.find_element(By.XPATH, "./ancestor::div[contains(@class, 'field') or contains(@data-field-name, '')][1]")
                        parent_name = parent_div.get_attribute("data-field-name") or parent_div.get_attribute("data-name")
                        if parent_name:
                            name = parent_name
                    except:
                        pass
                
                # Strategy 3: Group by nearby question text
                if not name:
                    try:
                        # Look for preceding question text with more flexible xpath
                        question_elements = radio.find_elements(By.XPATH, "./preceding::*[contains(@class, 'question') or self::legend or self::label or contains(text(), '?')][position() <= 3]")
                        for question_element in question_elements:
                            question_text = question_element.text.strip()
                            if question_text and len(question_text) < 200 and '?' in question_text:
                                # Create a group name from question text
                                name = "question_" + str(hash(question_text))[:8]
                                break
                    except:
                        pass
                
                # Strategy 4: Try to find associated labels
                if not name:
                    try:
                        if radio_id:
                            label = driver.find_element(By.CSS_SELECTOR, f"label[for='{radio_id}']")
                            label_text = label.text.strip()
                            if label_text:
                                # Look for parent question
                                parent_question = label.find_element(By.XPATH, "./ancestor::*[contains(@class, 'question') or contains(@class, 'field')][1]")
                                question_text = parent_question.text.strip()
                                if question_text and len(question_text) < 200:
                                    name = "question_" + str(hash(question_text))[:8]
                    except:
                        pass
                
                # Fallback: Create unique group
                if not name:
                    name = f"radio_group_{len(groups)}"
            
            # Clean up name
            name = name.strip()
            if name:
                if name not in groups:
                    groups[name] = []
                groups[name].append(radio)
                print(f"     → Added to group '{name}'")
            else:
                print(f"     → Could not determine group name")
                
        except Exception as e:
            print(f"⚠️ Error processing radio button {i+1}: {str(e)}")
            continue
    
    print(f"📊 Organized into {len(groups)} radio groups:")
    for group_name, radios in groups.items():
        print(f"   - {group_name}: {len(radios)} options")
        # Also show the labels for debugging
        for j, radio in enumerate(radios):
            try:
                radio_id = radio.get_attribute("id") or ""
                if radio_id:
                    try:
                        label = driver.find_element(By.CSS_SELECTOR, f"label[for='{radio_id}']")
                        label_text = label.text.strip()
                        print(f"     Option {j+1}: {label_text}")
                    except:
                        print(f"     Option {j+1}: No label found for {radio_id}")
            except:
                continue
    
    return groups


def find_custom_radio_elements(driver):
    """Find custom radio-like elements (clickable divs, buttons, etc.)"""
    custom_groups = {}
    processed_elements = set()  # Track processed elements to avoid duplicates
    
    # Look for labels associated with already processed radio buttons first
    processed_radio_ids = set()
    try:
        existing_radios = driver.find_elements(By.CSS_SELECTOR, "input[type='radio']")
        for radio in existing_radios:
            radio_name = radio.get_attribute("name")
            if radio_name:
                processed_radio_ids.add(radio_name)
    except:
        pass
    
    # Look for common patterns in modern forms
    selectors = [
        # Start with most specific patterns first
        "label[for*='question']",  # Labels for questions (most reliable)
        "div[class*='radio'][class*='option']",  # Radio option divs
        "button[role='radio']",
        "div[role='radio']", 
        "[class*='radio-button']",
        "[class*='option-button']",
    ]
    
    print("🔍 Looking for custom radio-like elements...")
    
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            print(f"   Found {len(elements)} elements matching '{selector}'")
            
            for element in elements:
                # Skip if already processed
                element_id = id(element)
                if element_id in processed_elements:
                    continue
                    
                if element.is_displayed():
                    # Check if this is associated with an already processed radio group
                    for_attr = element.get_attribute("for") or ""
                    if for_attr and any(radio_id in for_attr for radio_id in processed_radio_ids):
                        print(f"   ⏭️ Skipping element associated with already processed radio: {for_attr}")
                        continue
                    
                    # Try to group by nearby question text or data attributes
                    group_name = get_custom_radio_group_name(driver, element)
                    if group_name:
                        if group_name not in custom_groups:
                            custom_groups[group_name] = []
                        
                        # Avoid adding duplicate elements to the same group
                        element_text = element.text.strip()
                        is_duplicate = False
                        for existing_elem in custom_groups[group_name]:
                            if existing_elem.text.strip() == element_text:
                                is_duplicate = True
                                break
                        
                        if not is_duplicate:
                            custom_groups[group_name].append(element)
                            processed_elements.add(element_id)
                        
        except Exception as e:
            print(f"⚠️ Error finding custom radio elements with selector '{selector}': {str(e)}")
    
    # Filter out groups that seem to be duplicates of regular radio buttons
    filtered_groups = {}
    for group_name, elements in custom_groups.items():
        # Skip if the group only has one element and it looks like a question text
        if len(elements) == 1 and ("?" in elements[0].text or len(elements[0].text) > 100):
            print(f"   ⏭️ Skipping single-element question group: {group_name}")
            continue
        filtered_groups[group_name] = elements
    
    print(f"📊 Found {len(filtered_groups)} custom radio groups after filtering:")
    for group_name, elements in filtered_groups.items():
        print(f"   - {group_name}: {len(elements)} options")
    
    return filtered_groups


def get_custom_radio_group_name(driver, element):
    """Get group name for custom radio element"""
    try:
        # Check for 'for' attribute in labels
        for_attr = element.get_attribute("for")
        if for_attr and "question" in for_attr:
            # Extract question ID from 'for' attribute
            parts = for_attr.split("_")
            if len(parts) >= 2:
                return f"{parts[0]}_{parts[1]}"
        
        # Look for data attributes
        data_question = element.get_attribute("data-question")
        if data_question:
            return data_question
            
        # Look for nearby question text
        try:
            question_element = element.find_element(By.XPATH, "./preceding::*[contains(text(), '?') or contains(@class, 'question')][1]")
            question_text = question_element.text.strip()
            if question_text and len(question_text) < 150:
                return f"custom_question_{hash(question_text) % 10000}"
        except:
            pass
            
    except Exception as e:
        pass
    
    return None


def handle_custom_radio_group(driver, group_name, elements, form_data):
    """Handle custom radio-like elements"""
    if not elements:
        return None
    
    print(f"🔘 Custom radio group '{group_name}' has {len(elements)} options:")
    
    options = []
    for i, element in enumerate(elements):
        try:
            element_text = element.text.strip()
            element_value = element.get_attribute("value") or element.get_attribute("data-value") or ""
            
            print(f"   {i+1}. {element_text} (value: {element_value})")
            
            options.append({
                "element": element,
                "text": element_text,
                "value": element_value
            })
        except Exception as e:
            print(f"⚠️ Error processing custom radio option {i+1}: {str(e)}")
    
    # Use the same intelligent selection logic
    selected_option = select_custom_radio_option_intelligently(group_name.lower(), options, form_data)
    
    if selected_option:
        try:
            # Scroll to element
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", selected_option["element"])
            time.sleep(0.5)
            
            # Try to click the custom element
            selected_option["element"].click()
            print(f"✅ Selected custom option: {selected_option['text']}")
            return selected_option["text"]
        except Exception as e:
            print(f"⚠️ Could not click custom radio option: {str(e)}")
    
    return None


def select_custom_radio_option_intelligently(group_context, options, form_data):
    """Select custom radio option using same logic as regular radio buttons"""
    # Analyze the text content to understand the question type
    all_text = " ".join([opt["text"].lower() for opt in options])
    
    # Enhanced context detection based on actual content
    enhanced_context = group_context
    if any(word in all_text for word in ["immigration", "sponsorship", "visa", "support"]):
        enhanced_context = "immigration_sponsorship_question"
    elif any(word in all_text for word in ["hybrid", "remote", "office", "wfh", "work"]):
        enhanced_context = "work_arrangement_question"  
    elif any(word in all_text for word in ["available", "start", "when", "days"]):
        enhanced_context = "availability_question"
    elif any(word in all_text for word in ["gender", "identity"]):
        enhanced_context = "gender_identity_question"
    
    print(f"🔍 Enhanced context for custom radio: '{enhanced_context}'")
    print(f"📝 All option texts: {[opt['text'][:50] + '...' if len(opt['text']) > 50 else opt['text'] for opt in options]}")
    
    # Filter out options that are clearly questions/headers (too long or contain question marks)
    valid_options = []
    for opt in options:
        text = opt["text"].strip()
        # Skip if it's a question text (contains ?, very long, or looks like a form label)
        if len(text) > 100 or "?" in text or text.count("\n") > 2:
            print(f"   ⏭️ Skipping question text: {text[:50]}...")
            continue
        # Skip if it's empty or just whitespace
        if not text or text.isspace():
            continue
        valid_options.append(opt)
    
    if not valid_options:
        print("⚠️ No valid options found after filtering")
        return None
    
    print(f"✅ Valid options after filtering: {[opt['text'] for opt in valid_options]}")
    
    # Apply intelligent selection to valid options
    formatted_options = [{"label_lower": opt["text"].lower(), "label": opt["text"], "element": opt["element"]} for opt in valid_options]
    selected = select_radio_option_intelligently(enhanced_context, formatted_options, form_data)
    
    if selected:
        # Find the original option that matches
        for opt in valid_options:
            if opt["text"] == selected["label"]:
                return opt
    
    return None


def handle_gender_checkbox_group(driver, checkboxes_in_group, form_data):
    """Handle gender identity checkbox groups (multiple selection allowed)"""
    try:
        print(f"🔍 Processing {len(checkboxes_in_group)} gender options...")
        
        # For gender identity, we typically want to select "Woman" based on our profile
        for i, cb in enumerate(checkboxes_in_group):
            checkbox_element = cb["element"]
            context_lower = cb["context"].lower()
            is_displayed = checkbox_element.is_displayed()
            is_selected = checkbox_element.is_selected()
            
            print(f"   Option {i+1}: '{cb['context'][:30]}...', displayed={is_displayed}, selected={is_selected}")
            
            # Look for "Man" option (handle hidden checkboxes like radio buttons)
            if "man" in context_lower and "woman" not in context_lower:
                print(f"🎯 Found 'Man' option, attempting to select...")
                if not checkbox_element.is_selected():
                    success = False
                    
                    # If checkbox is visible, try direct click first
                    if checkbox_element.is_displayed():
                        try:
                            checkbox_element.click()
                            print("✅ Clicked checkbox directly")
                            success = True
                        except Exception as e1:
                            print(f"❌ Direct click failed: {str(e1)}")
                    
                    # Try JavaScript click
                    if not success:
                        try:
                            driver.execute_script("arguments[0].click();", checkbox_element)
                            print("✅ Clicked checkbox via JavaScript")
                            success = True
                        except Exception as e2:
                            print(f"❌ JavaScript click failed: {str(e2)}")
                    
                    # Try clicking the label (works for hidden elements)
                    if not success and cb["id"]:
                        try:
                            label = driver.find_element(By.CSS_SELECTOR, f"label[for='{cb['id']}']")
                            label.click()
                            print("✅ Clicked via label (hidden checkbox)")
                            success = True
                        except Exception as e3:
                            print(f"❌ Label click failed: {str(e3)}")
                    
                    if success:
                        print(f"✅ Selected gender identity: {cb['context']}")
                        return cb["context"]
                    else:
                        print("❌ Failed to select 'Man' option")
                else:
                    print("ℹ️ 'Man' option was already selected")
                    return cb["context"]
        
        # Fallback: select "I don't wish to answer" if "Man" not found
        for cb in checkboxes_in_group:
            checkbox_element = cb["element"]
            context_lower = cb["context"].lower()
            
            if any(phrase in context_lower for phrase in ["don't wish", "prefer not", "no answer"]) and checkbox_element.is_displayed():
                if not checkbox_element.is_selected():
                    try:
                        checkbox_element.click()
                    except:
                        try:
                            driver.execute_script("arguments[0].click();", checkbox_element)
                        except:
                            if cb["id"]:
                                label = driver.find_element(By.CSS_SELECTOR, f"label[for='{cb['id']}']")
                                label.click()
                    
                    print(f"✅ Selected gender identity (fallback): {cb['context']}")
                    return cb["context"]
        
    except Exception as e:
        print(f"⚠️ Error in gender checkbox selection: {str(e)}")
    
    return None


def handle_radio_group(driver, group_name, radio_buttons, form_data):
    """Handle a group of radio buttons with enhanced context detection"""
    group_context = group_name.lower()
    
    # Get context from labels and surrounding elements
    options = []
    for radio in radio_buttons:
        try:
            radio_id = radio.get_attribute("id")
            value = radio.get_attribute("value") or ""
            
            # Try multiple methods to find label text
            label_text = get_radio_label_text(driver, radio, radio_id)
            
            options.append({
                "element": radio,
                "value": value,
                "label": label_text,
                "label_lower": label_text.lower()
            })
        except Exception as e:
            print(f"⚠️ Error processing radio button: {str(e)}")
            continue
    
    if not options:
        print("⚠️ No radio options found")
        return None
    
    print(f"🔘 Radio group '{group_name}' has {len(options)} options:")
    for i, opt in enumerate(options):
        print(f"   {i+1}. {opt['label']}")
    
    # Smart selection based on enhanced context analysis
    selected_option = select_radio_option_intelligently(group_context, options, form_data)
    
    # Click the selected option
    if selected_option:
        try:
            radio_element = selected_option["element"]
            
            # If radio button is hidden, try to click its label instead
            if not radio_element.is_displayed():
                print(f"📍 Radio button is hidden, trying to click associated label...")
                label_clicked = try_click_radio_label(driver, radio_element)
                if label_clicked:
                    print(f"✅ Selected (via label): {selected_option['label']}")
                    return selected_option["label"]
            else:
                # Scroll to element first
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", radio_element)
                time.sleep(0.5)
                
                # Try multiple click methods
                try:
                    radio_element.click()
                except:
                    driver.execute_script("arguments[0].click();", radio_element)
                
                print(f"✅ Selected: {selected_option['label']}")
                return selected_option["label"]
                
        except Exception as e:
            print(f"⚠️ Could not select radio option: {str(e)}")
    
    return None


def try_click_radio_label(driver, radio_element):
    """Try to click the label associated with a hidden radio button"""
    try:
        radio_id = radio_element.get_attribute("id")
        if radio_id:
            # Try to find label with for attribute
            label = driver.find_element(By.CSS_SELECTOR, f"label[for='{radio_id}']")
            if label.is_displayed():
                label.click()
                return True
    except:
        pass
    
    try:
        # Try to find parent label
        parent_label = radio_element.find_element(By.XPATH, "./ancestor::label[1]")
        if parent_label.is_displayed():
            parent_label.click()
            return True
    except:
        pass
    
    try:
        # Try JavaScript click on the radio element itself
        driver.execute_script("arguments[0].click();", radio_element)
        return True
    except:
        pass
    
    return False


def extract_question_text_from_radio(driver, radio):
    """Extract the actual question text for a radio button group"""
    try:
        radio_name = radio.get_attribute("name") or ""
        radio_id = radio.get_attribute("id") or ""
        
        # Method 1: Look for preceding elements with question-like text
        try:
            # Find elements that contain question text (ending with ? or containing key words)
            preceding_elements = driver.find_elements(By.XPATH, 
                f"//input[@name='{radio_name}'][1]/preceding::*[contains(text(), '?') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'experience') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'immigration') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'hybrid') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'available')]")
            
            for elem in reversed(preceding_elements[-5:]):  # Check last 5 elements
                text = elem.text.strip()
                if text and len(text) > 10 and len(text) < 300:  # Reasonable question length
                    # Clean up the text
                    if text.endswith('*'):
                        text = text[:-1].strip()
                    return text
        except:
            pass
        
        # Method 2: Look for fieldset legend or form labels
        try:
            parent_fieldset = radio.find_element(By.XPATH, "./ancestor::fieldset[1]")
            legend = parent_fieldset.find_element(By.TAG_NAME, "legend")
            legend_text = legend.text.strip()
            if legend_text and len(legend_text) > 10 and len(legend_text) < 300:
                return legend_text
        except:
            pass
        
        # Method 3: Look for divs or labels with question-related classes/IDs
        try:
            question_selectors = [
                f"*[id*='question'][id*='{radio_name.split('_')[-1] if '_' in radio_name else radio_name}']",
                ".question-text", ".field-title", ".form-question", 
                f"label[for*='{radio_name}']",
                "h3", "h4", "h5"
            ]
            
            for selector in question_selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for elem in elements:
                        text = elem.text.strip()
                        if text and len(text) > 10 and len(text) < 300:
                            # Check if it's a question-like text
                            if "?" in text or any(word in text.lower() for word in 
                                                ["experience", "immigration", "hybrid", "available", "salary", "years", "need", "do you"]):
                                return text
                except:
                    continue
        except:
            pass
        
        # Method 4: Look in parent container text
        try:
            parent = radio.find_element(By.XPATH, "..")
            parent_text = parent.text.strip()
            
            if parent_text and len(parent_text) > 10:
                # Look for question patterns in parent text
                lines = parent_text.split('\n')
                for line in lines:
                    line = line.strip()
                    if line and len(line) > 10 and len(line) < 300:
                        if "?" in line or any(word in line.lower() for word in 
                                           ["experience", "immigration", "hybrid", "available", "salary", "years", "need", "do you"]):
                            return line
        except:
            pass
        
        # Method 5: Fallback to common patterns based on field name
        if "30515459002" in radio_name:
            return "Do you need, or will you need in the future, any immigration-related support or sponsorship from Glovo in order to begin the employment at the work location?"
        elif "30515462002" in radio_name:
            return "Glovo's hybrid ways of working mean 3 days in the office, and 2 days WFH, does this match your preferences or requirements?"
        elif "30515463002" in radio_name:
            return "Please indicate your English proficiency level"
        elif "30515464002" in radio_name:
            return "Please indicate your experience level"
        elif "30515465002" in radio_name:
            return "Please indicate your experience level"
        elif "30515466002" in radio_name:
            return "Please indicate your experience level"
        elif "31264548002" in radio_name:
            return "When would you be available to start?"
            
    except Exception as e:
        print(f"⚠️ Error extracting question text: {str(e)}")
    
    return None


def get_radio_label_text(driver, radio, radio_id):
    """Get comprehensive label text for a radio button"""
    label_text = ""
    
    # Method 1: Associated label element
    if radio_id:
        try:
            label = driver.find_element(By.CSS_SELECTOR, f"label[for='{radio_id}']")
            label_text = label.text.strip()
            if label_text and len(label_text) > 3:  # Avoid single letters/numbers
                return label_text
        except:
            pass
    
    # Method 2: Parent label element
    try:
        parent_label = radio.find_element(By.XPATH, "./ancestor::label[1]")
        label_text = parent_label.text.strip()
        if label_text and len(label_text) > 3:
            return label_text
    except:
        pass
    
    # Method 3: Find nearby question text using DOM traversal
    try:
        # Look for preceding elements with question-like text (contains ?)
        preceding_elements = driver.find_elements(By.XPATH, f"//input[@id='{radio_id}']/preceding::*[contains(text(), '?')]")
        for elem in reversed(preceding_elements[-3:]):  # Check last 3 elements
            text = elem.text.strip()
            if text and "?" in text and len(text) < 200:
                return text
    except:
        pass
    
    # Method 4: Look for question text in parent container's data attributes or IDs
    try:
        radio_name = radio.get_attribute("name") or ""
        if "question" in radio_name:
            # Try to find a div or element with similar name that contains question text
            possible_selectors = [
                f"*[id*='{radio_name}']",
                f"*[data-field*='{radio_name}']",
                f"fieldset legend", 
                "div.question-text",
                "div.field-title",
                "h3", "h4", "h5"  # Common heading tags for questions
            ]
            
            for selector in possible_selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for elem in elements:
                        text = elem.text.strip()
                        if text and ("?" in text or "experience" in text.lower() or "years" in text.lower() or 
                                   "immigration" in text.lower() or "hybrid" in text.lower() or "available" in text.lower()):
                            if len(text) < 200:
                                return text
                except:
                    continue
    except:
        pass
    
    # Method 5: Following sibling text
    try:
        sibling = radio.find_element(By.XPATH, "./following-sibling::*[1]")
        label_text = sibling.text.strip()
        if label_text and len(label_text) > 3:
            return label_text
    except:
        pass
    
    # Method 6: Parent container text but filter better
    try:
        parent = radio.find_element(By.XPATH, "..")
        parent_text = parent.text.strip()
        # Look for question-like text in parent
        if parent_text and ("?" in parent_text or any(word in parent_text.lower() for word in 
                           ["experience", "years", "immigration", "hybrid", "available", "salary"])):
            # Try to extract just the question part
            lines = parent_text.split('\n')
            for line in lines:
                if line.strip() and ("?" in line or any(word in line.lower() for word in 
                                   ["experience", "years", "immigration", "hybrid", "available"])):
                    if len(line.strip()) < 200:
                        return line.strip()
            # Fallback to first reasonable line
            if len(parent_text) < 200:
                return parent_text
    except:
        pass
    
    # Method 7: Value attribute with better naming for common patterns
    value = radio.get_attribute("value") or ""
    radio_name = radio.get_attribute("name") or ""
    
    # Create more meaningful names based on common patterns
    if "29495816002" in radio_name:
        return "Do you need immigration sponsorship?" + (f" (Option {value})" if value else "")
    elif "29495819002" in radio_name:
        return "Would you be interested in hybrid/remote work?" + (f" (Option {value})" if value else "")
    elif "31264560002" in radio_name:
        return "When are you available to start?" + (f" (Option {value})" if value else "")
    
    return value or f"Radio option for {radio_name}"


def select_radio_option_intelligently(group_context, options, form_data):
    """Intelligently select radio option based on context and form data"""
    
    # Immigration/sponsorship questions
    if any(word in group_context for word in ["immigration", "sponsorship", "visa", "sponsor", "support", "29495816002"]):
        print("🛂 Detected immigration/sponsorship question")
        # Look for "No" answers first (we don't need sponsorship)
        for option in options:
            if any(word in option["label_lower"] for word in ["no", "não", "nein", "not need", "don't need", "do not need"]):
                return option
        # If no clear "No", look for EU/citizen status
        for option in options:
            if any(word in option["label_lower"] for word in ["eu", "citizen", "european", "legal"]):
                return option
    
    # Gender identity questions
    elif any(word in group_context for word in ["gender", "identity", "sex"]):
        print("🚹🚺 Detected gender identity question")
        # Look for woman/female first
        for option in options:
            if any(word in option["label_lower"] for word in ["woman", "mujer", "female", "she", "her"]):
                return option
        # Fallback to "prefer not to answer"
        for option in options:
            if any(phrase in option["label_lower"] for phrase in ["don't wish", "prefer not", "no answer", "not specify"]):
                return option
    
    # Work arrangement/hybrid questions
    elif any(word in group_context for word in ["hybrid", "remote", "office", "work", "wfh", "arrangement", "29495819002"]):
        print("🏢 Detected work arrangement question")
        # Look for "Yes" to hybrid/remote work (remote preferred, but hybrid up to 3 days is acceptable)
        for option in options:
            if any(word in option["label_lower"] for word in ["yes", "sí", "sim", "agree", "match"]):
                return option
    
    # Availability/start date questions
    elif any(word in group_context for word in ["available", "start", "begin", "availability", "31264560002"]):
        print("📅 Detected availability question")
        # Look for immediate availability options first (currently unemployed)
        for option in options:
            if any(phrase in option["label_lower"] for phrase in ["immediately", "asap", "right away", "now", "less than 15"]):
                return option
        # Fallback to very short timeframes
        for option in options:
            if any(phrase in option["label_lower"] for phrase in ["15-30", "less than 30", "1 month", "30 days"]):
                return option
        # Last resort: short notice periods
        for option in options:
            if any(phrase in option["label_lower"] for phrase in ["31-60", "1-2 month", "30-60"]):
                return option
    
    # Special arrangements questions
    elif any(word in group_context for word in ["special", "arrangement", "accommodation", "need"]):
        print("♿ Detected special arrangements question")
        # Usually answer "No" or "None"
        for option in options:
            if any(word in option["label_lower"] for word in ["no", "none", "not needed", "não"]):
                return option
    
    # Experience level questions (if they appear as radio buttons)
    elif any(word in group_context for word in ["experience", "level", "years", "skill"]):
        print("💼 Detected experience question")
        # Look for 3+ years or advanced level
        for option in options:
            if any(phrase in option["label_lower"] for phrase in ["3+", "3-5", "advanced", "expert", "senior"]):
                return option
    
    # Default selection strategy
    print("🎯 Using default selection strategy")
    # Avoid obvious placeholder/default options
    filtered_options = [opt for opt in options if not any(word in opt["label_lower"] for word in ["select", "choose", "please", "---", "...", "default"])]
    
    if filtered_options:
        # If we have good options, pick the middle one or second option
        if len(filtered_options) >= 2:
            return filtered_options[1]  # Often the most reasonable choice
        else:
            return filtered_options[0]
    
    # Last resort: pick first option
    return options[0] if options else None


def handle_workday_dropdown(driver, field_element, context):
    """Handle Workday-style custom dropdowns"""
    try:
        # Workday dropdowns often have a clickable div or button that opens options
        print(f"🔧 Attempting Workday dropdown handling for: {context}")
        
        # Special handling for phone number selectors with pre-selected items
        if any(keyword in context.lower() for keyword in ['phone', 'code', 'country', 'teléfono']):
            print("📱 Detected phone/country selector - checking for pre-selected items")
            
            # First, look for and remove any pre-selected items
            selected_items = driver.find_elements(By.CSS_SELECTOR, "[data-automation-id='selectedItem']")
            if selected_items:
                print(f"🗑️ Found {len(selected_items)} pre-selected items to remove")
                for item in selected_items:
                    try:
                        # Look for delete button within the selected item
                        delete_btn = item.find_element(By.CSS_SELECTOR, "[data-automation-id='DELETE_charm']")
                        item_text = item.text.strip()
                        delete_btn.click()
                        print(f"✅ Removed pre-selected item: {item_text}")
                        time.sleep(0.5)
                    except Exception as e:
                        print(f"⚠️ Could not remove pre-selected item: {str(e)}")
            
            # Now try to search for Spain
            try:
                # Look for search box input
                search_inputs = driver.find_elements(By.CSS_SELECTOR, "[data-automation-id='searchBox']")
                if search_inputs:
                    search_input = search_inputs[0]
                    search_input.click()
                    time.sleep(0.5)
                    search_input.clear()
                    search_input.send_keys("Spain")
                    time.sleep(1)
                    
                    # Look for Spain option in dropdown
                    options = driver.find_elements(By.CSS_SELECTOR, "[data-automation-id='promptOption']")
                    for option in options:
                        option_text = option.text.lower()
                        if 'spain' in option_text or 'españa' in option_text or '+34' in option_text:
                            print(f"✅ Found Spain option: {option.text}")
                            option.click()
                            return option.text
                
            except Exception as e:
                print(f"⚠️ Error with search method: {str(e)}")
        
        # Try clicking the dropdown to open it
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", field_element)
        time.sleep(0.5)
        
        # Try different click methods
        try:
            field_element.click()
        except:
            try:
                driver.execute_script("arguments[0].click();", field_element)
            except:
                print("⚠️ Could not click Workday dropdown")
                return None
        
        time.sleep(1)  # Wait for dropdown to open
        
        # Look for dropdown options that appeared
        option_selectors = [
            "div[role='option']",
            "li[role='option']", 
            "div[data-automation-id*='option']",
            "button[role='option']",
            ".css-*[role='option']",  # Workday often uses CSS classes
            "div[id*='option']"
        ]
        
        for selector in option_selectors:
            try:
                options = driver.find_elements(By.CSS_SELECTOR, selector)
                if options:
                    print(f"📋 Found {len(options)} Workday dropdown options with selector: {selector}")
                    
                    # For country dropdowns, look for Spain
                    if any(word in context.lower() for word in ["country", "location"]):
                        for option in options:
                            option_text = option.text.strip()
                            if any(country in option_text.lower() for country in ["spain", "españa", "es"]):
                                print(f"🌍 Found Spain option: {option_text}")
                                try:
                                    option.click()
                                    print(f"✅ Selected Workday dropdown option: {option_text}")
                                    return option_text
                                except:
                                    try:
                                        driver.execute_script("arguments[0].click();", option)
                                        print(f"✅ Selected Workday dropdown option: {option_text}")
                                        return option_text
                                    except:
                                        continue
                    
                    # For other dropdowns, use intelligent selection
                    for option in options:
                        option_text = option.text.strip()
                        if option_text and not any(placeholder in option_text.lower() for placeholder in ["select", "choose", "please"]):
                            try:
                                option.click()
                                print(f"✅ Selected Workday dropdown option: {option_text}")
                                return option_text
                            except:
                                continue
                    break
            except Exception as e:
                continue
        
    except Exception as e:
        print(f"⚠️ Error handling Workday dropdown: {str(e)}")
    
    return None


def handle_dropdown(driver, select_field, context, form_data):
    """Handle dropdown/select fields with enhanced intelligence"""
    try:
        from selenium.webdriver.support.ui import Select
        select = Select(select_field)
        options = select.options
        
        if not options:
            print("⚠️ No dropdown options found")
            return None
        
        context_lower = context.lower()
        option_texts = [opt.text.strip() for opt in options]
        
        print(f"📋 Dropdown '{context}' has {len(options)} options:")
        for i, opt_text in enumerate(option_texts):
            print(f"   {i+1}. {opt_text}")
        
        # Enhanced dropdown selection logic
        selected_option = select_dropdown_option_intelligently(context_lower, options, form_data)
        
        if selected_option:
            try:
                # Scroll to dropdown first
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", select_field)
                time.sleep(0.5)
                
                select.select_by_visible_text(selected_option.text)
                print(f"✅ Selected dropdown option: {selected_option.text}")
                
                # If this was a country selection, trigger any dependent field updates
                if any(word in context_lower for word in ["country", "location"]):
                    time.sleep(1)  # Wait for any JS to update dependent fields
                    # Trigger change event to update phone code fields
                    driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", select_field)
                    time.sleep(0.5)
                
                return selected_option.text
            except Exception as e:
                print(f"⚠️ Could not select dropdown option: {str(e)}")
                
                # If standard dropdown failed, try Workday handling
                if "workday" in driver.current_url.lower():
                    print("🔧 Trying Workday-specific dropdown handling...")
                    return handle_workday_dropdown(driver, select_field, context)
                
    except Exception as e:
        print(f"⚠️ Error handling dropdown: {str(e)}")
        
        # If standard select handling failed completely, try Workday handling
        if "workday" in driver.current_url.lower():
            print("🔧 Trying Workday-specific dropdown handling...")
            return handle_workday_dropdown(driver, select_field, context)
    
    return None


def select_dropdown_option_intelligently(context_lower, options, form_data):
    """Intelligently select dropdown option based on context"""
    
    # Language proficiency questions
    if any(word in context_lower for word in ["english", "language", "proficiency"]):
        print("🗣️ Detected English proficiency question")
        preferred_levels = ["advanced", "fluent", "proficient", "expert", "professional"]
        for level in preferred_levels:
            for option in options:
                if level in option.text.lower():
                    return option
    
    # Experience years questions
    elif any(word in context_lower for word in ["experience", "years", "años"]):
        print("💼 Detected experience years question")
        # Look for tech stack specific experience
        if any(tech in context_lower for tech in ["python", "sql", "analytics", "data"]):
            preferred_exp = ["3+ years", "3+", "2-3 years", "4+", "5+", "advanced"]
        else:
            preferred_exp = ["2-3 years", "3+ years", "1-3 years", "intermediate"]
        
        for exp in preferred_exp:
            for option in options:
                if exp.lower() in option.text.lower():
                    return option
    
    # Availability/start date questions
    elif any(word in context_lower for word in ["available", "start", "when", "availability"]):
        print("📅 Detected availability question")
        preferred_times = ["31-60 days", "1-2 months", "30-60 days", "1 month", "15-30 days"]
        for time_option in preferred_times:
            for option in options:
                if time_option.lower() in option.text.lower():
                    return option
    
    # Salary range questions (though less common in dropdowns)
    elif any(word in context_lower for word in ["salary", "compensation", "pay"]):
        print("💰 Detected salary question")
        # Look for ranges around 60-70k EUR
        preferred_ranges = ["60", "65", "70", "50-60", "60-70", "55-65"]
        for salary_range in preferred_ranges:
            for option in options:
                if salary_range in option.text.lower():
                    return option
    
    # Education level questions
    elif any(word in context_lower for word in ["education", "degree", "university", "studies"]):
        print("🎓 Detected education question")
        preferred_education = ["master", "masters", "master's", "postgraduate", "university", "bachelor"]
        for edu in preferred_education:
            for option in options:
                if edu.lower() in option.text.lower():
                    return option
    
    # Location/country questions  
    elif any(word in context_lower for word in ["country", "location", "where", "ciudad", "nation", "región"]):
        print("🌍 Detected location question")
        # Get country from CV
        cv_country = get_country_from_cv()
        print(f"🌍 CV indicates country: {cv_country}")
        
        # Look for exact country matches first
        country_variations = {
            "Spain": ["spain", "españa", "es", "spanish"],
            "United Kingdom": ["united kingdom", "uk", "britain", "gb"],
            "Germany": ["germany", "deutschland", "de", "german"],
            "France": ["france", "francia", "fr", "french"],
            "Netherlands": ["netherlands", "holland", "nl", "dutch"]
        }
        
        if cv_country in country_variations:
            for variation in country_variations[cv_country]:
                for option in options:
                    if variation.lower() in option.text.lower():
                        print(f"✅ Found country match: {option.text}")
                        return option
        
        # Fallback to general location preference
        preferred_locations = ["spain", "barcelona", "españa", "europe", "eu"]
        for location in preferred_locations:
            for option in options:
                if location.lower() in option.text.lower():
                    return option
    
    # Notice period questions
    elif any(word in context_lower for word in ["notice", "period", "disponibilidad"]):
        print("⏰ Detected notice period question")
        preferred_periods = ["1 month", "30 days", "1-2 months", "immediate", "2 weeks"]
        for period in preferred_periods:
            for option in options:
                if period.lower() in option.text.lower():
                    return option
    
    # Work type/contract questions
    elif any(word in context_lower for word in ["contract", "type", "work type", "employment"]):
        print("📝 Detected contract type question")
        preferred_types = ["full time", "permanent", "indefinido", "full-time"]
        for contract_type in preferred_types:
            for option in options:
                if contract_type.lower() in option.text.lower():
                    return option
    
    # Default intelligent selection
    print("🎯 Using intelligent default selection")
    
    # Filter out obvious placeholder options
    filtered_options = []
    for option in options:
        option_text_lower = option.text.lower()
        # Skip placeholders and empty options
        if not any(placeholder in option_text_lower for placeholder in [
            "select", "choose", "please", "---", "...", "default", "pick", 
            "selecciona", "elige", "por favor", "ninguno"
        ]) and option.text.strip():
            filtered_options.append(option)
    
    if filtered_options:
        # If we have multiple good options, pick a reasonable middle ground
        if len(filtered_options) >= 3:
            # Pick the second option (often a good middle ground)
            return filtered_options[1]
        elif len(filtered_options) == 2:
            # With two options, pick the second (first might be "basic/beginner")
            return filtered_options[1]
        else:
            # Only one good option
            return filtered_options[0]
    
    # Last resort: avoid first option (often placeholder) if possible
    if len(options) > 1:
        return options[1]
    elif len(options) == 1:
        return options[0]
    
    return None

def process_individual_job(driver, job_id):
    """Process a single job application"""
    try:
        job_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
        print(f"\n🔍 Processing job: {job_url}")
        
        original_window = driver.current_window_handle
        driver.get(job_url)
        time.sleep(2)
        
        # Extract job title - this is important for matching on external sites
        try:
            job_title = driver.find_element(By.CSS_SELECTOR, ".job-details-jobs-unified-top-card__job-title").text.strip()
            print(f"📋 LinkedIn job title: {job_title}")
        except:
            job_title = "Unknown Title"
            print("⚠️ Could not extract job title from LinkedIn")
        
        # Extract job description and analyze
        job_description = extract_job_description(driver)
        job_analysis = None
        if job_description and not (TEST_MODE and TEST_SKIP_RELEVANCE):
            cv_content = load_cv_text(CV_FILE_PATH)
            relevance_analysis = analyze_job_relevance_with_gemini(job_description, cv_content)
            job_analysis = relevance_analysis  # Store for salary estimation
                print(f"✅ Job relevance analysis: {relevance_analysis}")
            if not relevance_analysis.get("relevant", False):
                print("⏭️ Skipping non-relevant job.")
                return
        elif TEST_MODE and TEST_SKIP_RELEVANCE:
            print("🧪 TEST MODE: Skipping relevance check to test apply button")

        # Handle Easy Apply or external application
        if find_and_click_apply_button(driver, job_title): # This now returns True for both Easy Apply and external
            
            # Check if it was an Easy Apply that was successfully submitted
            if "easy apply" in driver.page_source.lower() and "submitt" in driver.page_source.lower():
                 # Successfully submitted Easy Apply
                trello_key = os.getenv('TRELLO_KEY')
                trello_token = os.getenv('TRELLO_TOKEN')
                trello_list_id = os.getenv('TRELLO_APPLIED_LIST_ID')
                if trello_key and trello_token and trello_list_id:
                    trello_client = TrelloClient(api_key=trello_key, token=trello_token)
                    try:
                        applied_list = trello_client.get_list(trello_list_id)
                        card_desc = f"URL: {job_url}\nStatus: Applied (Easy Apply)\nDescription: {job_description[:500]}..."
                        applied_list.add_card(name=job_title, desc=card_desc)
                        print("✅ Created Trello card for applied job")
                    except Exception as e:
                        print(f"⚠️ Failed to create Trello card: {str(e)}")
            
            # Handle external multi-page application process
            else:
        form_data = load_form_data()
                cv_content = load_cv_text(CV_FILE_PATH)
                
                # Loop through application pages
                previous_url = None
                consecutive_skips = 0
                
                for i in range(5): # Max 5 pages to prevent infinite loops
                    print(f"➡️ On application page {i+1}")
                handle_cookies_popup(driver)
                    
                    current_url = driver.current_url
                    
                    # Fill forms and upload CV on the current page
                    filled_forms = analyze_and_fill_form(driver, form_data, job_analysis, job_title)
                    
                    # If newsletter form was skipped, continue to next iteration
                    if filled_forms == ["newsletter_skipped"]:
                        print("⏭️ Newsletter form was skipped, continuing to next page")
                        consecutive_skips += 1
                        
                        # Check if we're stuck in a loop (same URL after skip)
                        if current_url == previous_url:
                            print(f"⚠️ Same URL after skip attempt: {current_url}")
                            if consecutive_skips >= 3:
                                print("❌ Stuck in newsletter skip loop - ending application process")
                                break
            else:
                            consecutive_skips = 0  # Reset if URL changed
                        
                        previous_url = current_url
                        continue
                    elif filled_forms == ["newsletter_detected_but_not_skipped"]:
                        print("⚠️ Newsletter form detected but couldn't skip - trying to continue anyway")
                        # Don't upload CV or try to submit, just continue to next page
                        continue
                    
                    # Reset consecutive skips if we processed a real form
                    consecutive_skips = 0
                    previous_url = current_url
                    
                    upload_cv(driver)
                    
                    # Show dry run preview if enabled
                    dry_run_preview(driver, filled_forms)
                    
                    # Try to find and click the next button
                    if not click_next_or_submit_button(driver):
                        print("✅ Reached the end of the application flow.")
                        # Create Trello card after finishing external application
                        # (Assuming success if it completes the loop)
                        # ... Trello card creation logic ...
                        break
                    
                    time.sleep(3) # Wait for next page to load

            # Cleanup: close new tabs and switch back
            if len(driver.window_handles) > 1:
                for window_handle in driver.window_handles:
                    if window_handle != original_window:
                        driver.switch_to.window(window_handle)
                        driver.close()
                driver.switch_to.window(original_window)
            
    except Exception as e:
        print(f"❌ Error processing job {job_id}: {str(e)}")
        # Clean up any extra tabs
        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[0])

def process_direct_url(driver, url):
    """Process a job application from direct URL (for testing)"""
    try:
        print(f"\n🎯 Processing direct URL: {url}")
        
        driver.get(url)
        time.sleep(3)
        
        # Handle cookies popup
        handle_cookies_popup(driver)
        
        # Load form data
        form_data = load_form_data()
        
        # Extract job title from page
        try:
            job_title = driver.find_element(By.CSS_SELECTOR, "h1").text.strip()
        except:
            job_title = "Test Job"
        
        print(f"📋 Job title: {job_title}")
        
        # Fill forms and upload CV (no job analysis for direct URL testing)
        filled_forms = analyze_and_fill_form(driver, form_data, None, job_title)
        upload_cv(driver)
        
        # Show dry run preview
        dry_run_preview(driver, filled_forms)
        
        if DRY_RUN:
            print("🧪 DRY RUN COMPLETE: Form filled but not submitted")
        else:
            # Try to submit if not in dry run mode
            if click_next_or_submit_button(driver):
                print("✅ Application submitted successfully!")
            else:
                print("⚠️ Could not find submit button")
                
    except Exception as e:
        print(f"❌ Error processing direct URL: {str(e)}")


def process_saved_jobs(driver):
    """Process all saved jobs across pages"""
    try:
        print("\n📑 Loading saved jobs page...")
        driver.get(SAVED_JOBS_URL)
        time.sleep(3)
        
        if TEST_MODE:
            print("🧪 TEST MODE: Processing first 3 jobs from page 1")
            # Just get first page and first 3 jobs
            scroll_saved_jobs(driver)
            page_job_ids = extract_job_ids_from_page(driver)
            if page_job_ids:
                jobs_to_process = min(3, len(page_job_ids))  # Process 3 jobs or fewer if less available
                print(f"📊 Found {len(page_job_ids)} jobs on page 1. Processing first {jobs_to_process} jobs.")
                
                for i in range(jobs_to_process):
                    job_id = page_job_ids[i]
                    print(f"🎯 Testing with job {i+1}/{jobs_to_process} (ID: {job_id})")
                    process_individual_job(driver, job_id)
                    if i < jobs_to_process - 1:  # Don't wait after the last job
                        print("⏸️ Waiting 3 seconds before next job...")
                        time.sleep(3)
                
                print("✅ Test completed!")
            else:
                print("❌ No jobs found on page 1")
            return
        
        # Normal mode - collect all jobs from all pages
        all_job_ids = []
        page = 1
        
        # First, collect all job IDs from all pages
        while True:
            print(f"\n📄 Collecting jobs from page {page}")
            
            # Scroll to load all jobs on current page
            scroll_saved_jobs(driver)
            
            # Extract job IDs
            page_job_ids = extract_job_ids_from_page(driver)
            if not page_job_ids:
                print("No more jobs found on this page.")
                break
            
            all_job_ids.extend(page_job_ids)
            print(f"📊 Found {len(page_job_ids)} jobs on page {page}. Total collected: {len(all_job_ids)}")
            
            # Try next page
            if not go_to_next_page(driver):
                print("No more pages to process.")
                break
                
            page += 1
            time.sleep(2)
            
        # Now, process each job one by one
        print(f"\n✅ Collected a total of {len(all_job_ids)} unique job IDs. Starting processing...")
        for job_id in set(all_job_ids):
            process_individual_job(driver, job_id)
            time.sleep(random.uniform(2, 4)) # Wait between processing jobs
        
    except Exception as e:
        print(f"❌ Critical error in process_saved_jobs: {str(e)}")

def main():
    print("\n🤖 Starting Application Form Analyzer...")
    driver = None
    
    try:
        # Setup Chrome options to handle cookies
        options = uc.ChromeOptions()
        options.add_argument("--start-maximized")
        # This preference tries to reject non-essential cookies
        options.add_experimental_option(
            "prefs", {
                "profile.cookie_controls_mode": 1  # 0=Allow all, 1=Block third-party, 2=Block all
            }
        )
        driver = uc.Chrome(options=options)
        
        # Check if we're testing a direct URL
        if TEST_MODE and TEST_URL:
            print("🧪 TEST MODE: Using direct URL")
            process_direct_url(driver, TEST_URL)
        else:
            # Sign in to LinkedIn
        sign_in(driver)
        
        # Process saved jobs
        process_saved_jobs(driver)
        
    except Exception as e:
        print(f"❌ Critical error: {str(e)}")
    finally:
        if driver:
            driver.quit()
        print("\n✅ Script completed")

if __name__ == "__main__":
    main() 