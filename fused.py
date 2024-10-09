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
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
pb = Pushbullet(os.getenv("PUSHBULLET_API_KEY"))

# Rate limiter class to manage API requests
class RateLimiter:
    def __init__(self):
        self.hourly_request_times = deque()
        self.hourly_token_usage = deque()
        self.last_rate_limit_time = 0

    def add_request(self, tokens):
        current_time = time.time()
        self.hourly_request_times.append(current_time)
        self.hourly_token_usage.append(tokens)
        self.clean_old_entries()

    def clean_old_entries(self):
        current_time = time.time()
        while self.hourly_request_times and current_time - self.hourly_request_times[0] > 3600:
            self.hourly_request_times.popleft()
            self.hourly_token_usage.popleft()

    def handle_rate_limit(self):
        current_time = time.time()
        self.last_rate_limit_time = current_time
        self.clean_old_entries()
        
        tokens_last_hour = sum(self.hourly_token_usage)
        requests_last_hour = len(self.hourly_request_times)
        
        if tokens_last_hour >= TOKENS_PER_HOUR or requests_last_hour >= REQUESTS_PER_HOUR:
            wait_time = 3600 - (current_time - self.hourly_request_times[0])
            print(f"Rate limit reached. Waiting for {wait_time:.2f} seconds.")
            time.sleep(wait_time)
        else:
            print("Rate limit reached unexpectedly. Waiting for 60 seconds.")
            time.sleep(60)

rate_limiter = RateLimiter()

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
    while True:
        try:
            prompt = f"""
            CV Content:
            {cv_content}

            Job Title: {job['title']}
            Job Description:
            {job['description']}

            Tasks:
            1. Rate the relevancy of this job to the candidate's skills and experience on a scale of 0 to 10, where 0 is not relevant at all and 10 is extremely relevant.
            2. Write a very brief and targeted two-sentence personalized cover letter for this job based on the CV and job description.

            Provide your response in the following format:
            Score: [Your score as a single number between 0 and 10]
            Cover Letter: [Your two-sentence cover letter]
            """

            estimated_tokens = estimate_tokens(prompt)
            chat_completion = client.chat.completions.create(
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                model=MODEL,
            )
            rate_limiter.add_request(estimated_tokens)
            print("API call successful. Parsing response...")
            return chat_completion.choices[0].message.content
        except Exception as e:
            if "rate limit" in str(e).lower():
                rate_limiter.handle_rate_limit()
            else:
                raise

def parse_analysis(response):
    lines = response.split('\n')
    result = {}
    current_key = None
    for line in lines:
        if line.startswith("Score:"):
            result['score'] = int(line.split(":")[1].strip())
        elif line.startswith("Cover Letter:"):
            current_key = 'cover_letter'
            result[current_key] = "\n" + line.split(":", 1)[1].strip()  # Add a new line before the cover letter
        elif current_key:
            result[current_key] += " " + line.strip()
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
                    
                    # Wait for the "You applied on" element to appear (if it exists)
                    try:
                        applied_element = WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.XPATH, "//span[contains(@class, '_1j97a3y4y') and contains(text(), 'You applied on')]"))
                        )
                        print(f"Skipping job {job_id} as it was previously applied to")
                        continue
                    except TimeoutException:
                        # Element not found, proceed with job processing
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
                    analysis_result = analyze_job_relevance({'title': job_title, 'description': job_description}, cv_content)
                    parsed_result = parse_analysis(analysis_result)
                    
                    score = parsed_result['score']
                    cover_letter = parsed_result['cover_letter']
                    
                    if score < 8:  # Skip if relevancy score is below 8
                        print(f"Skipping job {job_title} due to low relevancy score: {score}")
                        continue

                    # Check if it's a Casual/Vacation position
                    is_casual = "Casual/Vacation" in driver.page_source
                    
                    # Apply Quick Apply filter only for non-Casual positions and non-perfect matches
                    if not is_casual and score < 10 and not is_quick_apply_job(driver):
                        print(f"Skipping job {job_id} as it's not a Quick Apply job and score is not 10")
                        continue

                    # Push Notification
                    pb.push_note(f"New Relevant Job: {job_title} - Score {score}", f"Job Link: {job_url}\n\nCover Letter:\n{cover_letter}")
                    
                    page_jobs.append({
                        'link': job_url,
                        'description': job_description,
                        'title': job_title,
                        'job_id': job_id,
                        'score': score,
                        'cover_letter': cover_letter
                    })
                    print(f"Successfully processed job: {job_title} with relevancy score: {score}")
                
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
