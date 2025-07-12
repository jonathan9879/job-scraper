import os
import time
import datetime
from dateutil.relativedelta import relativedelta
import json
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
from groq import Groq
from pushbullet import Pushbullet
from collections import deque
from dotenv import load_dotenv
import google.generativeai as genai

# Constants
# Update file paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT_FILE = os.path.join(SCRIPT_DIR, "job_scorer_checkpoint.json")
ANALYZED_JOBS_FILE = os.path.join(SCRIPT_DIR, "scraped_jobs.json")
SCORED_JOBS_FILE = os.path.join(SCRIPT_DIR, "scored_jobs.json")
CV_FILE_PATH = os.path.join(SCRIPT_DIR, "cv_text.txt")
GRAPH_FILE = os.path.join(SCRIPT_DIR, "job_score_distribution.png")
SCRAPED_JOBS_FILE = os.path.join(SCRIPT_DIR, "scraped_jobs.json")


PAGE_LOAD_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 5
MODEL = "llama-3.1-70b-versatile"
TOKENS_PER_MINUTE = 5000
TOKENS_PER_HOUR = 131072
REQUESTS_PER_MINUTE = 30
REQUESTS_PER_HOUR = 1800

CITY_URLS = [
    "https://www.seek.com.au/data-jobs/in-All-Sydney-NSW/contract-temp?daterange=7&worktype=245%2C244",
    "https://www.seek.com.au/data-jobs/in-All-Brisbane-QLD/contract-temp?daterange=7&worktype=245%2C244",
    "https://www.seek.com.au/data-jobs/in-All-Melbourne-VIC/contract-temp?daterange=7&worktype=245%2C244"
]


# Load environment variables
load_dotenv()

# LLM API setup
pb = Pushbullet(os.getenv("PUSHBULLET_API_KEY")) if os.getenv("PUSHBULLET_API_KEY") else None

# Initialize Gemini client
if os.getenv("GEMINI_API_KEY"):
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    gemini_model = genai.GenerativeModel('gemini-2.5-pro-exp-03-25') # Use the specified experimental model
    print("✅ Gemini client initialized")
else:
    gemini_model = None
    print("⚠️ Gemini API key not found, analysis will be skipped.")

def sign_in(driver):
    print("Signing in...")
    try:
        # Wait for the page to load completely
        time.sleep(10)
        
        print(f"Current URL: {driver.current_url}")
        
        # Try multiple methods to find the sign-in link
        sign_in_selectors = [
            (By.CSS_SELECTOR, 'a[data-automation="sign in"]'),
            (By.XPATH, "//a[contains(text(), 'Sign in')]"),
            (By.XPATH, "//a[contains(@href, '/oauth/login')]"),
            (By.LINK_TEXT, "Sign in"),
            (By.PARTIAL_LINK_TEXT, "Sign in")
        ]
        
        sign_in_link = None
        for selector in sign_in_selectors:
            try:
                sign_in_link = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable(selector)
                )
                print(f"Found sign-in link using selector: {selector}")
                break
            except:
                print(f"Selector {selector} failed")
        
        if sign_in_link is None:
            print("Could not find sign-in link. Dumping page source.")
            with open("page_source.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            raise Exception("Sign-in link not found")
        
        print("Clicking sign-in link...")
        driver.execute_script("arguments[0].click();", sign_in_link)
        time.sleep(5)
        
        print(f"Current URL after clicking sign-in: {driver.current_url}")

        # Enter email
        print("Entering email...")
        email_field = WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
            EC.presence_of_element_located((By.ID, "emailAddress"))
        )
        email_field.clear()
        email_field.send_keys(os.getenv("EMAILADDRESS"))

        # Enter password
        print("Entering password...")
        password_field = WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
            EC.presence_of_element_located((By.ID, "password"))
        )
        password_field.clear()
        password_field.send_keys(os.getenv("EMAILPASSWORD"))

        # Click sign in button
        print("Clicking sign-in button...")
        sign_in_button = WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[data-cy="login"]'))
        )
        sign_in_button.click()
        time.sleep(10)  # Wait for login to complete

        print("Successfully signed in")
    except TimeoutException as e:
        print(f"Timeout error during sign-in: {e}")
        print(f"Current URL: {driver.current_url}")
        raise
    except NoSuchElementException as e:
        print(f"Element not found during sign-in: {e}")
        print(f"Current URL: {driver.current_url}")
        raise
    except ElementClickInterceptedException as e:
        print(f"Element click intercepted during sign-in: {e}")
        print(f"Current URL: {driver.current_url}")
        raise
    except Exception as e:
        print(f"Unexpected error during sign-in: {e}")
        print(f"Current URL: {driver.current_url}")
        raise

def save_checkpoint(jobs, page_number, city_url):
    checkpoint_data = {
        "jobs": jobs,
        "page_number": page_number,
        "city_url": city_url
    }
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(checkpoint_data, f)

def load_checkpoint():
    try:
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def save_analyzed_jobs(jobs):
    with open(SCRAPED_JOBS_FILE, 'w') as f:
        json.dump(jobs, f)

def extract_job_ids_from_page(driver):
    page_source = driver.page_source
    job_id_pattern = r'job-title-(\d+)'
    job_ids = re.findall(job_id_pattern, page_source)
    return list(set(job_ids))

# New function to load CV from file
def load_cv_text(cv_file_path):
    with open(cv_file_path, 'r') as f:
        cv_content = f.read()
    return cv_content

def estimate_tokens(text):
    return len(text) // 3

def is_quick_apply_job(driver):
    try:
        # Wait for the apply button to be present
        apply_button = WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[data-automation="job-detail-apply"]'))
        )
        
        # Check if the button text is "Quick apply"
        return apply_button.text.strip().lower() == "quick apply"
    except:
        return False

def analyze_job_relevance(job, cv_content):
    print("\n🧠 Analyzing job relevance using Gemini...")
    if not gemini_model:
        print("❌ Gemini client not initialized. Skipping analysis.")
        # Return a default structure that parse_analysis can handle
        return "Relevance Score: 0\nInterest Score: 0\nKey Match Reason: Skipped analysis - Gemini API key missing.\nCover Letter: Skipped analysis - Gemini API key missing."

    try:
        # Refined prompt with Interest Score and Key Match Reason
        prompt = f"""
        **Candidate Profile & Preferences:**
        {cv_content}
        Key Preferences: Seeking Data Scientist/Analyst roles, ideally contract/temp in Sydney/Brisbane/Melbourne. Prefers remote or hybrid (Barcelona-based acceptable). Strong preference for roles involving Python, SQL, Cloud (AWS/GCP/Azure), MLOps, ETL, and ML frameworks. Values innovation, clear project goals, and opportunities for skill growth. Minimum 6-month contracts preferred unless exceptionally interesting. Avoid roles strictly requiring Australian Citizenship unless explicitly stated otherwise in the description.

        **Job Details:**
        Title: {job['title']}
        Link: {job.get('link', 'N/A')}  # Include link if available
        Description:
        {job['description']}

        **Analysis Task:**
        1.  **Relevance Score (0-10):** Based *only* on the alignment between the CV/Preferences and the Job Description, how relevant is this role? (10 = perfect match, 0 = irrelevant). Consider skills, experience level, contract length/type, location/remote options, and preferred technologies.
        2.  **Interest Score (0-10):** Beyond direct skill match, how *interesting* does this role seem based on the description? Consider factors like innovation, project scope, learning opportunities, company description (if available), and overall appeal.
        3.  **Key Match Reason:** Briefly explain the single most compelling reason this job *is* a good match (or why it *isn't* if the score is low). Focus on the strongest alignment or mismatch.
        4.  **Cover Letter Snippet:** Write a concise, targeted two-sentence snippet highlighting the candidate's most relevant qualification(s) for *this specific* job.

        **Output Format (Strict):**
        Relevance Score: [Number 0-10]
        Interest Score: [Number 0-10]
        Key Match Reason: [Your concise explanation]
        Cover Letter: [Your two-sentence snippet]
        """

        print("⚡ Sending request to Gemini API...")
        response = gemini_model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.3
            )
        )

        # Ensure response.text is accessed correctly
        if response.parts:
            response_text = "".join(part.text for part in response.parts)
            print("✅ Gemini API call successful. Parsing response...")
            return response_text
        else:
            print("⚠️ Gemini API response was empty or blocked.")
            print(f"Prompt Feedback: {response.prompt_feedback}")
            # Return a default structure that parse_analysis can handle
            return "Relevance Score: 0\nInterest Score: 0\nKey Match Reason: Error: No response from API or content blocked.\nCover Letter: Error: No response from API or content blocked."

    except Exception as e:
        print(f"❌ Error during Gemini API call: {e}")
        # Return a default structure that parse_analysis can handle
        return f"Relevance Score: 0\nInterest Score: 0\nKey Match Reason: Error during analysis: {e}\nCover Letter: Error during analysis: {e}"

def parse_analysis(response):
    lines = response.strip().split('\n')
    result = {
        'relevance_score': 0,
        'interest_score': 0,
        'key_match_reason': 'Parsing Error',
        'cover_letter': 'Parsing Error'
    }
    current_key = None
    buffer = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("Relevance Score:"):
            if current_key:
                result[current_key] = " ".join(buffer).strip()
            current_key = 'relevance_score'
            buffer = [line.split(":", 1)[1].strip()]
        elif line.startswith("Interest Score:"):
            if current_key:
                result[current_key] = " ".join(buffer).strip()
            current_key = 'interest_score'
            buffer = [line.split(":", 1)[1].strip()]
        elif line.startswith("Key Match Reason:"):
            if current_key:
                result[current_key] = " ".join(buffer).strip()
            current_key = 'key_match_reason'
            buffer = [line.split(":", 1)[1].strip()]
        elif line.startswith("Cover Letter:"):
            if current_key:
                result[current_key] = " ".join(buffer).strip()
            current_key = 'cover_letter'
            buffer = [line.split(":", 1)[1].strip()]
        elif current_key:
            buffer.append(line)

    # Save the last key
    if current_key:
        result[current_key] = " ".join(buffer).strip()

    # Attempt to convert scores to int, default to 0 on failure
    try:
        result['relevance_score'] = int(result['relevance_score'])
    except (ValueError, TypeError):
        result['relevance_score'] = 0
    try:
        result['interest_score'] = int(result['interest_score'])
    except (ValueError, TypeError):
        result['interest_score'] = 0

    print(f"Parsed Analysis: Relevance={result['relevance_score']}, Interest={result['interest_score']}") # Debug print
    return result

def is_date_within_six_months(date_str):
    today = datetime.date.today()
    six_months_from_now = today + relativedelta(months=6)
    
    # List of month names
    months = ['january', 'february', 'march', 'april', 'may', 'june', 
              'july', 'august', 'september', 'october', 'november', 'december']
    
    # Convert date string to lowercase for case-insensitive matching
    date_str = date_str.lower()
    
    # Extract day and month from the date string
    day = int(re.search(r'\d+', date_str).group())
    month = next((i+1 for i, m in enumerate(months) if m in date_str), None)
    
    if month is None:
        return False
    
    # Assume the year is the next occurrence of this date
    year = today.year if (month > today.month or (month == today.month and day >= today.day)) else today.year + 1
    
    date = datetime.date(year, month, day)
    return date <= six_months_from_now

def get_job_listings(driver, resume_from_checkpoint=False, cv_content="", city_url=""):
    jobs = []
    page_number = 1
    consecutive_empty_pages = 0
    max_empty_pages = 3  # Stop after 3 consecutive empty pages

    if resume_from_checkpoint:
        checkpoint = load_checkpoint()
        if checkpoint:
            jobs = checkpoint["jobs"]
            page_number = checkpoint["page_number"]
            city_url = checkpoint["city_url"]
            print(f"Resuming from checkpoint at page {page_number} for {city_url}")

    while True:
        print(f"Scraping page {page_number} for {city_url}")
        for attempt in range(MAX_RETRIES):
            try:
                expected_url = f"{city_url}&page={page_number}"
                if driver.current_url != expected_url:
                    print(f"Navigating to page: {expected_url}")
                    driver.get(expected_url)
                    time.sleep(10)

                no_results = driver.find_elements(By.XPATH, "//h3[contains(text(), 'No matching search results')]")
                if no_results:
                    print("No more results found. Stopping scraping.")
                    return jobs

                job_ids = extract_job_ids_from_page(driver)
                print(f"Found {len(job_ids)} job IDs on this page")

                if not job_ids:
                    consecutive_empty_pages += 1
                    if consecutive_empty_pages >= max_empty_pages:
                        print(f"No job IDs found on {max_empty_pages} consecutive pages. Stopping scraping.")
                        return jobs
                    print("No job IDs found on this page. Moving to next page.")
                    break
                else:
                    consecutive_empty_pages = 0

                page_jobs = []
                for job_id in job_ids:
                    job_url = f"https://www.seek.com.au/job/{job_id}"
                    print(f"Processing job ID: {job_id}")
                    
                    driver.get(job_url)
                    WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(EC.presence_of_element_located((By.CSS_SELECTOR, '[data-automation="jobAdDetails"]')))       
                    
                    # Skip if already applied
                    try:
                        # Wait for either the green tick SVG or the "You applied on" text
                        applied_element = WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.XPATH, """
                                //div[@id='applied-date-message']//span[contains(@class, '_1j97a3y4y') and contains(@class, '_1j97a3yr') and contains(text(), 'You applied on')]
                                |
                                //svg[
                                    contains(@class, 'w75d4w1y') and
                                    ./path[1][contains(@d, 'M12 1C5.9 1 1 5.9 1 12s4.9 11 11 11 11-4.9 11-11S18.1 1 12 1zm0 20c-5 0-9-4-9-9s4-9 9-9 9 4 9 9-4 9-9 9z')] and
                                    ./path[2][contains(@d, 'M15.3 9.3 11 13.6l-1.3-1.3c-.4-.4-1-.4-1.4 0s-.4 1 0 1.4l2 2c.2.2.5.3.7.3s.5-.1.7-.3l5-5c.4-.4.4-1 0-1.4s-1-.4-1.4 0z')]
                                ]
                            """))
                        )
                        print(f"Skipping job {job_id} as it was previously applied to")
                        continue  # Skip to the next job in the loop
                    except TimeoutException:
                        # Neither element found, proceed with job processing
                        pass
                    
                    job_description = driver.find_element(By.CSS_SELECTOR, '[data-automation="jobAdDetails"]').text
                    job_title = driver.find_element(By.CSS_SELECTOR, '[data-automation="job-detail-title"]').text

                    # Check for "AUSTRALIAN CITIZEN" in any case combination
                    if re.search(r'australian\s+citizen', job_description, re.IGNORECASE):
                        print(f"Skipping job {job_id} due to Australian citizenship requirement")
                        continue

                    # Check for dates more than 6 months in the future
                    date_pattern = r'\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
                    dates = re.findall(date_pattern, job_description, re.IGNORECASE)
                    if any(not is_date_within_six_months(date) for date in dates):
                        print(f"Skipping job {job_id} due to contract length exceeding 6 months")
                        continue

                    # Check for phrases to skip in both title and description
                    skip_phrases = [
                        "9 month ", "9-month", "9month", "9 Month",
                        "12 month", "12-month", "12month", "12 Month",
                        "24 month", "24-month", "24month", "24 Month"
                    ]
                    if any(phrase in job_description or phrase in job_title for phrase in skip_phrases):
                        print(f"Skipping job due to matching phrase: {job_title}")
                        continue
                    
                    
                    # Analyze job relevance and get cover letter
                    analysis_result = analyze_job_relevance({'title': job_title, 'description': job_description, 'link': job_url}, cv_content)
                    parsed_result = parse_analysis(analysis_result)
                    
                    relevance_score = parsed_result['relevance_score']
                    interest_score = parsed_result['interest_score']
                    key_match_reason = parsed_result['key_match_reason']
                    cover_letter = parsed_result['cover_letter']
                    
                    # Adjusted filtering logic: Require high relevance OR high interest
                    # Example: Relevance >= 7 OR (Relevance >= 5 AND Interest >= 8)
                    if not (relevance_score >= 7 or (relevance_score >= 5 and interest_score >= 8)):
                        print(f"Skipping job {job_title} (Relevance: {relevance_score}, Interest: {interest_score}) - doesn't meet threshold.")
                        continue

                    # Check if it's a Casual/Vacation position
                    is_casual = "Casual/Vacation" in driver.page_source
                    
                    # Apply Quick Apply filter only for non-Casual positions and non-perfect matches
                    if not is_casual and relevance_score < 9 and interest_score < 9 and not is_quick_apply_job(driver):
                        print(f"Skipping job {job_id} as it's not a Quick Apply job and scores aren't high enough")
                        continue

                    # Push Notification
                    notification_title = f"⭐ Job Match: {job_title} (R:{relevance_score}/I:{interest_score})"
                    notification_body = (
                        f"Reason: {key_match_reason}\n"
                        f"Link: {job_url}\n\n"
                        f"Cover Snippet:\n{cover_letter}"
                    )
                    if pb:
                        pb.push_note(notification_title, notification_body)
                    else:
                         print("Pushbullet not configured, skipping notification.")
                    
                    page_jobs.append({
                        'link': job_url,
                        'description': job_description,
                        'title': job_title,
                        'job_id': job_id,
                        'relevance_score': relevance_score,
                        'interest_score': interest_score,
                        'key_match_reason': key_match_reason,
                        'cover_letter': cover_letter
                    })
                    print(f"✅ Successfully processed job: {job_title} (R:{relevance_score}, I:{interest_score})")
                
                jobs.extend(page_jobs)
                save_checkpoint(jobs, page_number, city_url)
                page_number += 1
                break
            except Exception as e:
                print(f"Error scraping page {page_number}, attempt {attempt + 1}: {e}")
                if attempt == MAX_RETRIES - 1:
                    print("Max retries reached. Moving to next page.")
                    page_number += 1
                    break
                time.sleep(RETRY_DELAY)

    return jobs

def main(resume_from_checkpoint=False, cv_file_path=CV_FILE_PATH):
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    
    try:
        driver.get("https://www.seek.com.au/")
        print(f"Initial page title: {driver.title}")
        print(f"Initial URL: {driver.current_url}")
        time.sleep(10)  # Increased wait time

        sign_in(driver)  # Sign in before starting the scrape

        # Load CV content
        cv_content = load_cv_text(cv_file_path)
        
        all_job_listings = []
        
        for city_url in CITY_URLS:
            print(f"Starting scrape for {city_url}")
            job_listings = get_job_listings(driver, resume_from_checkpoint=resume_from_checkpoint, cv_content=cv_content, city_url=city_url)
            all_job_listings.extend(job_listings)
            save_analyzed_jobs(all_job_listings)  # Save after each city
            
        print(f"Total jobs found across all cities: {len(all_job_listings)}")


    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main(resume_from_checkpoint=False, cv_file_path=CV_FILE_PATH)  # Resume if checkpoint exists
