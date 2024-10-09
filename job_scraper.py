import os
import time
import json
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException, StaleElementReferenceException

# Constants
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT_FILE = os.path.join(SCRIPT_DIR, "scraping_checkpoint.json")
SCRAPED_JOBS_FILE = os.path.join(SCRIPT_DIR, "scraped_jobs.json")
PAGE_LOAD_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 5


# ... existing imports and constants ...

from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException

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

# ... rest of the code remains unchanged ...




def save_checkpoint(jobs, page_number):
    checkpoint_data = {
        "jobs": jobs,
        "page_number": page_number
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

def get_job_listings(driver, resume_from_checkpoint=False):
    jobs = []
    page_number = 1

    if resume_from_checkpoint:
        checkpoint = load_checkpoint()
        if checkpoint:
            jobs = checkpoint["jobs"]
            page_number = checkpoint["page_number"]
            print(f"Resuming from checkpoint at page {page_number}")

    while True:
        print(f"Scraping page {page_number}")
        for attempt in range(MAX_RETRIES):
            try:
                expected_url = f"https://www.seek.com.au/data-jobs/in-All-Sydney-NSW/contract-temp?page={page_number}"
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
                    print("No job IDs found on this page. Moving to next page.")
                    break

                page_jobs = []
                for job_id in job_ids:
                    job_url = f"https://www.seek.com.au/job/{job_id}"
                    print(f"Processing job ID: {job_id}")
                    
                    driver.get(job_url)
                    
                    WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(EC.presence_of_element_located((By.CSS_SELECTOR, '[data-automation="jobAdDetails"]')))
                    job_description = driver.find_element(By.CSS_SELECTOR, '[data-automation="jobAdDetails"]').text
                    job_title = driver.find_element(By.CSS_SELECTOR, '[data-automation="job-detail-title"]').text
                    
                    # Check for phrases to skip
                    skip_phrases = [
                        "You applied on ",
                        "9 month ", "9-month", "9month", "9 Month",
                        "12 month", "12-month", "12month", "12 Month"
                    ]
                    if any(phrase in job_description for phrase in skip_phrases):
                        print(f"Skipping job due to matching phrase: {job_title}")
                        continue
                    
                    page_jobs.append({
                        'link': job_url,
                        'description': job_description,
                        'title': job_title,
                        'job_id': job_id
                    })
                    print(f"Successfully processed job: {job_title}")
                
                jobs.extend(page_jobs)
                save_checkpoint(jobs, page_number)
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

def main(resume_from_checkpoint=False):
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
        

        driver.get("https://www.seek.com.au/data-jobs/in-All-Sydney-NSW/contract-temp")
        time.sleep(5)

        jobs = get_job_listings(driver, resume_from_checkpoint)
        
        print(f"Total jobs scraped: {len(jobs)}")
        save_analyzed_jobs(jobs)
        
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    resume_from_checkpoint = False  # Set to True if you want to resume from checkpoint
    main(resume_from_checkpoint)

