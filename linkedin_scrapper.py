import requests
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
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
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException, StaleElementReferenceException
from groq import Groq
from pushbullet import Pushbullet
from collections import deque
from dotenv import load_dotenv
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium_stealth import stealth
import random
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from selenium.webdriver import ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException
import undetected_chromedriver as uc
import random
import time
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
import traceback
import google.generativeai as genai


proxies = [
    '193.123.228.230:8080',
    '45.79.139.48:1001',
    # Add more residential proxies
]

session = requests.Session()
retries = Retry(
    total=5,
    backoff_factor=0.1,
    status_forcelist=[429, 500, 502, 503, 504]
)
session.mount('https://', HTTPAdapter(max_retries=retries))



def rotate_proxy():
    return random.choice(proxies)


# Load environment variables first
load_dotenv()
print("✅ Environment variables loaded")

# Enable network tracking
caps = DesiredCapabilities.CHROME
caps['goog:loggingPrefs'] = {'performance': 'ALL'}

# Constants
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT_FILE = os.path.join(SCRIPT_DIR, "job_scorer_checkpoint.json")
SCRAPED_JOBS_FILE = os.path.join(SCRIPT_DIR, "scraped_jobs.json")
CV_FILE_PATH = os.path.join(SCRIPT_DIR, "cv_text.txt")
TOP_APPLICANT_URL = "https://www.linkedin.com/jobs/collections/top-applicant/"
RECOMMENDED_URL = "https://www.linkedin.com/jobs/collections/recommended/"
LINKEDIN_EASY_APPLY_FLAG = "⭐"

# Updated SELECTORS with verified 2024 LinkedIn structure
SELECTORS = {
    'job_list_container': [
        "div.jobs-search-results-list",  # New primary container
        "div.scaffold-layout__list",  # Fallback container
        "div.jobs-search-results-list__content"
    ],
    'job_cards': [
        "div.jobs-search-results__list-item",  # Updated card selector
        "li.job-card-container"  # Alternate list item format
    ],
    'job_id_attributes': ["data-job-id", "data-occludable-job-id"],
    'title': "a.job-card-list__title",
    'company': "span.job-card-container__primary-description",
    'location': "li.job-card-container__metadata-item",
    'link': "a.job-card-list__title",
    'description': ".jobs-description__content",
    'easy_apply': "button[aria-label='Easy Apply']"
}



# Initialize services
print("🛠️ Initializing services...")
pb = Pushbullet(os.getenv("PUSHBULLET_API_KEY")) if os.getenv("PUSHBULLET_API_KEY") else None
client = Groq(api_key=os.getenv("GROQ_API_KEY")) if os.getenv("GROQ_API_KEY") else None

# Initialize Gemini client
gemini_api_key = os.getenv("GEMINI_API_KEY")
if not gemini_api_key:
    print("⚠️ GEMINI_API_KEY not found in environment variables. AI features will be disabled.")
    gemini_model = None
else:
    NEW_GEMINI_MODEL_ID = "gemini-2.5-pro" # User specified model
    
    # Configure Gemini with environment variable
    genai.configure(api_key=gemini_api_key)
    gemini_model = genai.GenerativeModel(NEW_GEMINI_MODEL_ID)
    print(f"✅ Gemini client initialized with model: {NEW_GEMINI_MODEL_ID}")

print("✅ Services initialized")

# Configure stealth settings
def configure_stealth(driver):
    driver.execute_cdp_cmd('Network.setUserAgentOverride', {
        "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    })
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': '''
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        })
        '''
    })

def debug_shadow_dom(driver):
    """Check for shadow DOM elements"""
    print("\n🔍 Checking for shadow DOM...")
    try:
        shadow_host = driver.find_element(By.CSS_SELECTOR, "div.jobs-search-results-list")
        shadow_root = driver.execute_script("return arguments[0].shadowRoot", shadow_host)
        if shadow_root:
            print("⚠️ Found shadow DOM structure!")
            print("Shadow root children:", len(shadow_root.find_elements(By.XPATH, "*")))
            return shadow_root
        return None
    except Exception as e:
        print(f"ℹ️ No shadow DOM detected: {str(e)}")
        return None

def debug_container_structure(container):
    """Print detailed container information"""
    print("\n🔍 Container Structure Debug:")
    try:
        print(f"Tag: {container.tag_name}")
        print(f"Class: {container.get_attribute('class')}")
        print(f"Child count: {len(container.find_elements(By.XPATH, '*'))}")
        print("First 5 child elements:")
        for idx, child in enumerate(container.find_elements(By.XPATH, '*')[:5]):
            print(f"{idx+1}. {child.tag_name} class: {child.get_attribute('class')}")
    except Exception as e:
        print(f"⚠️ Container debug failed: {str(e)}")

def debug_print(element, name):
    """Enhanced debug printer with element visibility check"""
    if element:
        try:
            print(f"🔍 {name} found: {element.text[:50]}..." if element.text else f"🔍 {name} found (no text)")
            print(f"   Visible: {element.is_displayed()}, Enabled: {element.is_enabled()}")
        except StaleElementReferenceException:
            print(f"❌ {name} element went stale during inspection")
    else:
        print(f"❌ {name} not found")

def debug_page_structure(driver):
    """Print critical elements for debugging"""
    print("\n🔍 Debugging page structure...")
    try:
        html = driver.find_element(By.TAG_NAME, 'body').get_attribute('outerHTML')
        print(f"📄 Body snippet: {html[:2000]}...")  # First 2000 characters
    except Exception as e:
        print(f"❌ Failed to get page HTML: {str(e)}")

def wait_for_lazy_load(driver, selector, timeout=15):
    WebDriverWait(driver, timeout).until(
        lambda d: d.find_element(By.CSS_SELECTOR, selector).is_displayed()
    )
    time.sleep(0.5)  # Extra stabilization

def find_scrollable_container(driver):
    """Find the scrollable container with multiple detection methods"""
    print("\n🔍 Attempting to find scrollable container...")
    
    # Method 1: Try to find by random class pattern
    try:
        # Look for div with random-looking class that contains job cards
        random_class_containers = driver.find_elements(By.CSS_SELECTOR, "div[class*='jobs-search-results-list']")
        random_class_containers.extend(driver.find_elements(By.CSS_SELECTOR, "div[class^='jobs-search']"))
        
        for container in random_class_containers:
            job_cards = container.find_elements(By.CSS_SELECTOR, "div.job-card-container")
            if len(job_cards) > 0:
                print("✅ Found container with random class pattern")
                debug_container_structure(container)
                return container
    except Exception as e:
        print(f"Method 1 failed: {str(e)}")

    # Method 2: Find by structure (parent with specific child elements)
    try:
        scaffold = driver.find_element(By.CSS_SELECTOR, ".scaffold-layout__list")
        children = scaffold.find_elements(By.XPATH, "./div")
        
        for child in children:
            try:
                # Check if this div contains job cards and has a significant height
                size = child.size
                if size['height'] > 100:  # Likely a scrollable container
                    job_cards = child.find_elements(By.CSS_SELECTOR, "div.job-card-container")
                    if len(job_cards) > 0:
                        print("✅ Found container by structure")
                        debug_container_structure(child)
                        return child
            except:
                continue
    except Exception as e:
        print(f"Method 2 failed: {str(e)}")

    # Method 3: Find by scrollable properties
    try:
        # Find elements that might be scrollable
        elements = driver.find_elements(By.CSS_SELECTOR, "div[class*='jobs'], div[class*='results'], div[class*='list']")
        
        for element in elements:
            try:
                # Check if element has overflow properties
                overflow = driver.execute_script("""
                    let style = window.getComputedStyle(arguments[0]);
                    return {
                        overflow: style.overflow,
                        overflowY: style.overflowY,
                        height: style.height,
                        maxHeight: style.maxHeight
                    };
                """, element)
                
                # Check if element has scroll properties
                if ('scroll' in overflow['overflow'] or 
                    'scroll' in overflow['overflowY'] or 
                    'auto' in overflow['overflow'] or 
                    'auto' in overflow['overflowY']):
                    
                    job_cards = element.find_elements(By.CSS_SELECTOR, "div.job-card-container")
                    if len(job_cards) > 0:
                        print("✅ Found container by scroll properties")
                        debug_container_structure(element)
                        return element
            except:
                continue
    except Exception as e:
        print(f"Method 3 failed: {str(e)}")

    print("❌ Could not find scrollable container with any method")
    return None

def scroll_container_method(driver, container):
    """Scroll using the container element"""
    print("\n📜 Container scrolling started...")
    last_height = driver.execute_script("return arguments[0].scrollHeight", container)
    jobs_count = 0
    scroll_attempts = 0
    max_attempts = 20
    
    while scroll_attempts < max_attempts:
        try:
            # Try multiple scroll methods
            scroll_methods = [
                # Method 1: Smooth scroll
                """
                arguments[0].scroll({
                    top: arguments[0].scrollHeight,
                    behavior: 'smooth'
                });
                """
            ]
            
            for method in scroll_methods:
                driver.execute_script(method, container)
                time.sleep(random.uniform(1.0, 1.5))
            
            # Get new scroll height and job count
            new_height = driver.execute_script("return arguments[0].scrollHeight", container)
            job_cards = container.find_elements(By.CSS_SELECTOR, "div.job-card-container")
            current_count = len(job_cards)
            
            print(f"Scroll height: {new_height} (was: {last_height})")
            print(f"Visible jobs: {current_count}")
            
            # Check if we've loaded a full page (usually 25 jobs)
            if current_count >= 24:  # LinkedIn typically shows 25 jobs per page
                print("✅ Found full page of jobs")
                return current_count
            
            if new_height == last_height:
                scroll_attempts += 1
                print(f"No height change (attempt {scroll_attempts}/{max_attempts})")
            else:
                print("New content loaded")
                last_height = new_height
                scroll_attempts = 0
            
        except Exception as e:
            print(f"❌ Scroll error: {str(e)}")
            scroll_attempts += 1
    
    return len(job_cards)

def scroll_fallback_method(driver):
    """Fallback scrolling method using mouse wheel simulation"""
    print("\n📜 Fallback scrolling started with mouse simulation...")
    jobs_count = 0
    scroll_attempts = 0
    max_attempts = 15
    
    # Get window size
    window_size = driver.get_window_size()
    window_height = window_size['height']
    window_width = window_size['width']
    
    # Calculate initial scroll position (75% right, middle height)
    scroll_x = int(window_width * 0.75)
    scroll_y = int(window_height * 0.5)
    
    try:
        # Move mouse to initial position
        action = ActionChains(driver)
        action.move_by_offset(scroll_x, scroll_y).perform()
        
        while scroll_attempts < max_attempts:
            # Get current job count
            current_jobs = driver.find_elements(By.CSS_SELECTOR, "div.job-card-container")
            current_count = len(current_jobs)
            
            # Perform scrolling action
            for _ in range(random.randint(2, 4)):
                # Scroll down with mouse wheel
                driver.execute_script("""
                    window.scrollBy({
                        top: arguments[0],
                        behavior: 'smooth'
                    });
                """, random.randint(300, 500))
                
                # Small pause between scroll actions
                time.sleep(random.uniform(0.3, 0.7))
            
            # Wait for content to load
            time.sleep(random.uniform(1.0, 1.5))
            
            # Check for new jobs
            new_jobs = driver.find_elements(By.CSS_SELECTOR, "div.job-card-container")
            new_count = len(new_jobs)
            
            print(f"Visible jobs: {new_count} (was: {current_count})")
            
            if new_count > current_count:
                print(f"Found {new_count - current_count} new jobs")
                jobs_count = new_count
                scroll_attempts = 0
                
                # Move mouse slightly for realism
                if random.random() < 0.3:
                    driver.execute_script("""
                        let e = new MouseEvent('mousemove', {
                            clientX: arguments[0],
                            clientY: arguments[1],
                            bubbles: true
                        });
                        document.elementFromPoint(arguments[0], arguments[1]).dispatchEvent(e);
                    """, scroll_x + random.randint(-20, 20), scroll_y + random.randint(-20, 20))
            else:
                scroll_attempts += 1
                print(f"No new jobs found (attempt {scroll_attempts}/{max_attempts})")
                
                # Try scrolling from a different position
                new_y = int(window_height * random.uniform(0.3, 0.7))
                driver.execute_script(f"window.scrollTo(0, {new_y});")
            
            if jobs_count >= 25:
                print("Reached target number of jobs")
                break
            
            # Occasionally scroll up slightly
            if random.random() < 0.2:
                driver.execute_script("window.scrollBy(0, -100);")
                time.sleep(random.uniform(0.5, 1.0))
            
    except Exception as e:
        print(f"❌ Scroll error: {str(e)}")
    
    return jobs_count

def sign_in(driver):
    print("\n🔐 Starting LinkedIn sign-in process...")
    try:
        print("1. Navigating to login page...")
        driver.get("https://www.linkedin.com/login")
        time.sleep(2)
        print(f"Current URL: {driver.current_url}")
        
        print("\n2. Locating email field...")
        email_field = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((
                By.CSS_SELECTOR, 
                'input[id="username"][name="session_key"][aria-label="Email or phone"]'
            ))
        )
        print("✅ Email field found")
        print(f"Email field properties:")
        print(f"- Displayed: {email_field.is_displayed()}")
        print(f"- Enabled: {email_field.is_enabled()}")
        print(f"- ID: {email_field.get_attribute('id')}")
        print(f"- Current value: '{email_field.get_attribute('value')}'")
        
        print("\n3. Focusing email field...")
        driver.execute_script("""
            arguments[0].focus();
            arguments[0].scrollIntoView({block: 'center'});
        """, email_field)
        
        # Check if field is focused
        active_element = driver.switch_to.active_element
        print(f"Currently focused element ID: {active_element.get_attribute('id')}")
        print(f"Is email field focused? {active_element.get_attribute('id') == 'username'}")
        
        print("\n4. Clearing email field...")
        email_field.clear()
        print(f"Field value after clear: '{email_field.get_attribute('value')}'")
        
        print("\n5. Getting email from environment...")
        email = os.getenv("LINKEDIN_EMAIL")
        if not email:
            driver.save_screenshot("login_failure.png")
            raise Exception("LinkedIn email not found in environment variables")
        print(f"Email length: {len(email)} characters")
        
        print("\n6. Typing email character by character...")
        for i, char in enumerate(email, 1):
            email_field.send_keys(char)
            current_value = email_field.get_attribute('value')
            print(f"Character {i}/{len(email)} typed. Current field value: '{current_value}'")
            time.sleep(0.1)
        
        # Verify email entry
        print("\n7. Verifying email entry...")
        final_value = email_field.get_attribute('value')
        print(f"Final email field value: '{final_value}'")
        print(f"Expected email value: '{email}'")
        if final_value != email:
            raise Exception(f"Email verification failed. Expected: {email}, Got: {final_value}")
        
        # Check for error messages
        print("\n8. Checking for error messages...")
        try:
            error_message = driver.find_element(By.ID, "error-for-username")
            if error_message.is_displayed():
                print(f"⚠️ Error message found: {error_message.text}")
        except NoSuchElementException:
            print("No error message found")
        
        print("\n9. Locating password field...")
        password_field = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "password"))
        )
        print("✅ Password field found")
        print(f"Password field displayed: {password_field.is_displayed()}")
        
        print("\n10. Entering password...")
        password_field.clear()
        password = os.getenv("LINKEDIN_PASSWORD")
        if not password:
            raise Exception("LinkedIn password not found in environment variables")
        password_field.send_keys(password)
        print("Password entered (length: {len(password)} characters)")
        
        print("\n11. Locating sign in button...")
        sign_in_button = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((
                By.CSS_SELECTOR,
                'button[type="submit"][aria-label="Sign in"]'
            ))
        )
        print("✅ Sign in button found")
        print(f"Button text: {sign_in_button.text}")
        print(f"Button enabled: {sign_in_button.is_enabled()}")
        
        print("\n12. Clicking sign in button...")
        time.sleep(1)  # Small pause before clicking
        driver.execute_script("arguments[0].click();", sign_in_button)
        print("✅ Sign-in button clicked")
        
        print("\n13. Waiting for login completion...")
        time.sleep(3)
        
        # Check for security checkpoint
        current_url = driver.current_url
        if "checkpoint/challenge" in current_url:
            print("\n🔒 Security checkpoint detected!")
            print("Waiting for manual verification...")
            print("Please complete the security verification in the browser.")
            
            # Wait for manual verification (5 minutes)
            verification_timeout = 300  # seconds
            verification_start = time.time()
            
            while "checkpoint/challenge" in driver.current_url:
                if time.time() - verification_start > verification_timeout:
                    raise Exception("Manual verification timeout exceeded (5 minutes)")
                print("⏳ Waiting for verification... (Press Ctrl+C to cancel)")
                time.sleep(5)
            
            print("✅ Security verification completed!")
            time.sleep(3)  # Wait for redirect to complete
        
        # Verify successful login
        if "feed" in driver.current_url or "mynetwork" in driver.current_url:
            print("✅ Successfully logged in!")
            return True
        else:
            print(f"⚠️ Unexpected URL after login: {driver.current_url}")
            driver.save_screenshot("login_failure.png")
            raise Exception("Login failed - unexpected landing page")
            
    except Exception as e:
        print(f"❌ Login failed: {str(e)}")
        print("\nFinal page state:")
        print(f"Current URL: {driver.current_url}")
        print(f"Page title: {driver.title}")
        print("Visible error messages:")
        try:
            error_messages = driver.find_elements(By.CSS_SELECTOR, ".error, .alert, .notification")
            for msg in error_messages:
                print(msg.text)
        except:
            pass
        driver.save_screenshot("login_failure.png")
        raise

def is_easy_apply_job(driver):
    try:
        print("🔍 Checking for Easy Apply button...")
        button = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Easy Apply']")
        debug_print(button, "Easy Apply button")
        return True
    except NoSuchElementException:
        print("❌ No Easy Apply button found")
        return False

def send_push_notification(job):
    notification_text = (
        f"{LINKEDIN_EASY_APPLY_FLAG if job['easy_apply'] else ''} "
        f"{job['title']} at {job['company']}\n"
        f"Score: {job['score']}\n"
        f"URL: {job['url']}\n"
        f"Apply URL: {job['apply_url'] or 'N/A'}"
    )
    pb.push_note("New Job Match", notification_text)

def scroll_to_load_all_jobs(driver):
    print("📜 Starting to scroll for jobs...")
    scrolls = 0
    last_jobs_count = 0
    max_scrolls = 20  # Prevent infinite scrolling
    
    while scrolls < max_scrolls:
        # Scroll down
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)  # Wait for new content
        
        # Get current jobs count
        job_cards = driver.find_elements(By.CSS_SELECTOR, "div.job-card-container")
        current_jobs_count = len(job_cards)
        
        print(f"📊 Found {current_jobs_count} jobs after scroll {scrolls + 1}")
        
        # If no new jobs loaded after scroll, break
        if current_jobs_count == last_jobs_count:
            print("No new jobs loaded, stopping scroll")
            break
            
        last_jobs_count = current_jobs_count
        scrolls += 1
    
    return driver.find_elements(By.CSS_SELECTOR, "div.job-card-container")

def extract_job_details(driver):
    """Extract all details for a job listing including Easy Apply status"""
    try:
        # Wait for job details to load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".job-details-jobs-unified-top-card__job-title"))
        )
        
        # Extract basic details with multiple selector attempts
        title_selectors = [
            ".job-details-jobs-unified-top-card__job-title",
            "h1.t-24",
            ".jobs-unified-top-card__job-title"
        ]
        
        title = None
        for selector in title_selectors:
            try:
                title = driver.find_element(By.CSS_SELECTOR, selector).text.strip()
                if title:
                    break
            except:
                continue
        
        if not title:
            raise Exception("Could not find job title")
            
        # Company name with multiple selectors
        company_selectors = [
            ".job-details-jobs-unified-top-card__company-name",
            ".jobs-unified-top-card__company-name",
            ".jobs-company__name"
        ]
        
        company = None
        for selector in company_selectors:
            try:
                company_element = driver.find_element(By.CSS_SELECTOR, selector)
                company = company_element.text.strip()
                if company:
                    break
            except:
                continue
                
        if not company:
            print("❌ Could not find company name")
            company = "Unknown Company"
        
        # Location with multiple selectors
        location_selectors = [
            ".job-details-jobs-unified-top-card__primary-description-container .t-black--light",
            ".jobs-unified-top-card__bullet",
            ".jobs-unified-top-card__workplace-type"
        ]
        
        location = None
        for selector in location_selectors:
            try:
                location_element = driver.find_element(By.CSS_SELECTOR, selector)
                location = location_element.text.split('·')[0].strip()
                if location:
                    break
            except:
                continue
                
        if not location:
            print("❌ Could not find location")
            location = "Unknown Location"
        
        # Description with multiple selectors
        description_selectors = [
            ".jobs-description__content",
            ".jobs-box__html-content",
            ".jobs-description-content__text"
        ]
        
        description = None
        for selector in description_selectors:
            try:
                description = driver.find_element(By.CSS_SELECTOR, selector).text.strip()
                if description:
                    break
            except:
                continue
                
        if not description:
            print("❌ Could not find description")
            description = "No description available"
        
        # Check for Easy Apply button
        is_easy_apply = False
        apply_link = None
        try:
            # Look for Easy Apply button in the top card
            apply_button = driver.find_element(By.CSS_SELECTOR, ".jobs-apply-button--top-card button")
            button_text = apply_button.text.strip().lower()
            
            if "easy apply" in button_text:
                print("✅ Found Easy Apply button")
                is_easy_apply = True
                apply_link = None  # No external link needed for Easy Apply
            else:
                # If not Easy Apply, try to get external apply link
                apply_link = (apply_button.get_attribute("data-apply-url") or 
                            apply_button.get_attribute("href") or 
                            driver.current_url)
                print("📎 Found external apply link")
        except Exception as e:
            print(f"⚠️ Error checking apply button: {str(e)}")
            apply_link = driver.current_url
        
        # Create job data dictionary
        job_data = {
            'job_id': driver.current_url.split('currentJobId=')[-1].split('&')[0],
            'title': title,
            'company': company,
            'location': location,
            'description': description,
            'easy_apply': is_easy_apply,
            'apply_link': apply_link if not is_easy_apply else None,
            'source_url': driver.current_url,
            'timestamp': datetime.datetime.now().isoformat()
        }
        
        print(f"Job details extracted: {title} at {company}")
        print(f"Easy Apply: {'Yes' if is_easy_apply else 'No'}")
        print(f"Job ID: {job_data['job_id']}")
        
        return job_data
        
    except Exception as e:
        print(f"❌ Error extracting job details: {str(e)}")
        traceback.print_exc()
        return None

def extract_job_ids(driver):
    """Extract job IDs with the correct format"""
    job_ids = set()
    print("\n🔍 Extracting job IDs...")
    
    # Try multiple selector patterns
    selectors = [
        "div.job-card-container",
        "li.jobs-search-results__list-item",
        "div.jobs-search-results-list__list-item"
    ]
    
    for selector in selectors:
        try:
            cards = driver.find_elements(By.CSS_SELECTOR, selector)
            for card in cards:
                try:
                    # Try multiple ways to get job ID
                    job_id = None
                    
                    # Method 1: Direct attribute
                    job_id = card.get_attribute('data-job-id')
                    
                    # Method 2: Occludable ID
                    if not job_id:
                        job_id = card.get_attribute('data-occludable-job-id')
                    
                    # Method 3: Extract from link
                    if not job_id:
                        link = card.find_element(By.CSS_SELECTOR, "a.job-card-list__title")
                        href = link.get_attribute('href')
                        if 'view' in href:
                            job_id = href.split('view/')[-1].split('/')[0]
                        elif 'currentJobId=' in href:
                            job_id = href.split('currentJobId=')[-1].split('&')[0]
                    
                    if job_id:
                        job_ids.add(job_id)
                        print(f"Found job ID: {job_id}")
                        
                except Exception as e:
                    print(f"Error extracting from card: {str(e)}")
                    continue
                    
            if job_ids:
                break  # If we found jobs with this selector, stop trying others
                
        except Exception as e:
            print(f"Selector {selector} failed: {str(e)}")
            continue
    
    return list(job_ids)

def process_job_card(driver, job_card):
    """Process a single job card"""
    try:
        # Get job ID from card
        job_id = None
        print("\n🔍 Attempting to extract job ID...")
        
        # Debug the job card HTML
        print("Job card HTML:")
        print(job_card.get_attribute('outerHTML')[:500])
        
        # Try multiple methods to get job ID
        selectors = [
            ".job-card-list__entity-lockup a[href]",  # Main job title link
            ".job-card-container__link[href]",
            ".job-card-list__title[href]",
            "a[data-control-name='job_card_title'][href]",
            ".job-card-list a[href]",  # Any link in the job card
            "a[href*='/jobs/view/']",  # Links containing view
            "a[href*='currentJobId=']"  # Links containing currentJobId
        ]
        
        for selector in selectors:
            try:
                links = job_card.find_elements(By.CSS_SELECTOR, selector)
                print(f"Found {len(links)} links with selector: {selector}")
                
                for link in links:
                    href = link.get_attribute('href')
                    print(f"Checking link: {href}")
                    
                    if href:
                        if '/jobs/view/' in href:
                            job_id = href.split('/jobs/view/')[-1].split('/')[0]
                        elif 'currentJobId=' in href:
                            job_id = href.split('currentJobId=')[-1].split('&')[0]
                        if job_id:
                            print(f"✅ Found job ID: {job_id}")
                            break
            except Exception as e:
                print(f"Selector {selector} failed: {str(e)}")
                continue
                
        # Fallback to direct attributes
        if not job_id:
            print("Trying direct attributes...")
            direct_attrs = ['data-job-id', 'data-occludable-job-id', 'data-entity-urn']
            for attr in direct_attrs:
                try:
                    value = job_card.get_attribute(attr)
                    if value:
                        if ':' in value:  # Handle URN format
                            job_id = value.split(':')[-1]
                        else:
                            job_id = value
                        print(f"✅ Found job ID from {attr}: {job_id}")
                        break
                except:
                    continue
        
        if not job_id:
            print("❌ Could not find job ID with any method")
            return None
            
        # Process the job details
        job_url = f"https://www.linkedin.com/jobs/collections/top-applicant/?currentJobId={job_id}"
        print(f"\n🔗 Processing job URL: {job_url}")
        
        driver.get(job_url)
        time.sleep(2)  # Wait for page load
        
        # Extract job details
        job_data = extract_job_details(driver)
        if job_data:
            with open(CV_FILE_PATH, 'r', encoding='utf-8') as f:
                cv_content = f.read()
            
            analysis = analyze_job_relevance(job_data, cv_content)
            job_data['analysis'] = analysis
            
            # If should_apply is True, try to save the job on LinkedIn
            if analysis.get('should_apply', False):
                try:
                    print("💾 Attempting to save recommended job...")
                    
                    # Try multiple selectors for the save button
                    save_button_selectors = [
                        "button.jobs-save-button",
                        "button.artdeco-button--secondary[type='button']",
                        "button.jobs-save-button.artdeco-button--secondary",
                        "//button[contains(@class, 'jobs-save-button')]",
                        "//button[.//span[contains(text(), 'Save')]]"
                    ]
                    
                    save_button = None
                    for selector in save_button_selectors:
                        try:
                            # Try CSS selector first
                            if selector.startswith("//"):
                                save_button = WebDriverWait(driver, 5).until(
                                    EC.presence_of_element_located((By.XPATH, selector))
                                )
                            else:
                                save_button = WebDriverWait(driver, 5).until(
                                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                                )
                            
                            # Check if button is not already in "Saved" state
                            button_text = save_button.text.lower()
                            if "saved" not in button_text and save_button.is_displayed() and save_button.is_enabled():
                                print(f"Found save button with text: {button_text}")
                                break
                            else:
                                print("Job already saved or button not interactive")
                                save_button = None
                                
                        except Exception:
                            continue
                    
                    if save_button:
                        # Scroll button into view
                        driver.execute_script(
                            "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", 
                            save_button
                        )
                        time.sleep(1)
                        
                        # Try multiple click methods
                        click_success = False
                        try:
                            # Method 1: Direct click with wait
                            WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.jobs-save-button"))).click()
                            click_success = True
                        except:
                            try:
                                # Method 2: JavaScript click
                                driver.execute_script("arguments[0].click();", save_button)
                                click_success = True
                            except:
                                try:
                                    # Method 3: Action chains with offset
                                    action = ActionChains(driver)
                                    action.move_to_element_with_offset(save_button, 5, 5)
                                    action.click()
                                    action.perform()
                                    click_success = True
                                except Exception as click_error:
                                    print(f"❌ All click methods failed: {str(click_error)}")
                        
                        if click_success:
                            print("✅ Job saved successfully on LinkedIn")
                            time.sleep(1)  # Wait for save to complete
                        else:
                            print("⚠️ Could not click save button")
                    else:
                        print("⚠️ Save button not found or already saved")
                        
                except Exception as save_error:
                    print(f"❌ Error saving job: {str(save_error)}")
            
            return job_data
            
        return None
        
    except Exception as e:
        print(f"❌ Error processing job card: {str(e)}")
        return None

def process_job_cards(driver, job_cards):
    """Process job cards with correct URL format"""
    processed_jobs = []
    
    # Extract job IDs
    job_ids = extract_job_ids(driver)
    print(f"✅ Found {len(job_ids)} job IDs\n")
    
    # Process each job
    for idx, job_id in enumerate(job_ids, 1):
        try:
            # Construct the correct URL format
            job_url = f"https://www.linkedin.com/jobs/collections/top-applicant/?currentJobId={job_id}"
            print(f"\n🔍 Processing job {idx}/{len(job_ids)}")
            print(f"URL: {job_url}")
            
            # Navigate to the job
            driver.get(job_url)
            
            # Wait for the job details to load
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".job-details-jobs-unified-top-card__job-title"))
                )
            except TimeoutException:
                print(f"⚠️ Timeout waiting for job details to load")
                continue
                
            # Add stabilization delay
            time.sleep(2)
            
            # Extract and save job details
            job_data = extract_job_details(driver)
            if job_data:
                processed_jobs.append(job_data)
                success = save_jobs([job_data], job_data.get('easy_apply', False))
                if success:
                    print("💾 Job saved successfully")
                else:
                    print("⚠️ Failed to save job")
            
            # Random delay between jobs
            delay = random.uniform(2, 4)
            print(f"Waiting {delay:.1f} seconds before next job...")
            time.sleep(delay)
            
        except Exception as e:
            print(f"❌ Error processing job {idx}: {str(e)}")
            continue
    
    return processed_jobs

def load_existing_job_ids():
    """Load job IDs from all JSON files"""
    existing_ids = set()
    
    # Track sources of job IDs for debugging
    sources = {
        'easy_apply_jobs.json': 0,
        'external_jobs.json': 0,
        'scraped_jobs.json': 0,
        'apply_jobs.json': 0
    }
    
    for filename in sources.keys():
        try:
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    try:
                        jobs = json.load(f)
                        # Try both 'id' and 'job_id' fields
                        new_ids = {job.get('job_id', job.get('id')) for job in jobs if job.get('job_id') or job.get('id')}
                        existing_ids.update(new_ids)
                        sources[filename] = len(new_ids)
                        print(f"📁 Found {len(new_ids)} jobs in {filename}")
                    except json.JSONDecodeError:
                        print(f"⚠️ {filename} was empty or corrupted")
        except Exception as e:
            print(f"⚠️ Error loading {filename}: {str(e)}")
    
    print("\n📊 Job ID sources summary:")
    for source, count in sources.items():
        print(f"- {source}: {count} jobs")
    print(f"Total unique jobs: {len(existing_ids)}")
    
    return existing_ids

def get_job_listings(driver, max_pages=5):
    """Get job listings from both collections"""
    all_jobs = []
    processed_job_ids = set()
    
    # Load existing job IDs first
    existing_ids = load_existing_job_ids()
    print(f"\n📚 Found {len(existing_ids)} previously processed jobs")
    
    # First process top applicant jobs
    print("\n🎯 Starting with Top Applicant collection...")
    top_applicant_jobs = process_job_collection(driver, TOP_APPLICANT_URL, processed_job_ids, existing_ids)
    all_jobs.extend(top_applicant_jobs)
    
    # Then process recommended jobs
    print("\n👥 Moving to Recommended collection...")
    recommended_jobs = process_job_collection(driver, RECOMMENDED_URL, processed_job_ids, existing_ids, max_pages)
    all_jobs.extend(recommended_jobs)
    
    return all_jobs

def process_job_collection(driver, base_url, processed_job_ids, existing_ids, max_pages=None):
    """Process jobs from a specific collection"""
    collection_jobs = []
    current_page = 1
    
    try:
        # Load CV content first
        cv_content = load_cv_text(CV_FILE_PATH)
        if not cv_content:
            raise Exception("Failed to load CV content")
            
        print(f"\n4. Navigating to {base_url}...")
        driver.get(base_url)
        time.sleep(3)
        
        while True:
            if max_pages and current_page > max_pages:
                print(f"Reached maximum pages ({max_pages}) for this collection")
                break
                
            print(f"\n📄 Processing page {current_page}")
            
            # Get jobs on current page
            scroll_container = find_scrollable_container(driver)
            if not scroll_container:
                print("❌ Could not find job container")
                break
            
            # Scroll and get job IDs
            job_cards = scroll_and_get_jobs(driver, scroll_container)
            job_ids_on_page = []
            
            print(f"\n🔍 Found {len(job_cards)} job cards on page")
            
            for card in job_cards:
                try:
                    job_id = extract_job_id(card)
                    if job_id:
                        if job_id in existing_ids:
                            print(f"⏭️ Skipping previously processed job {job_id}")
                        elif job_id in processed_job_ids:
                            print(f"⏭️ Skipping already seen job {job_id}")
                        else:
                            print(f"✨ New job found: {job_id}")
                            job_ids_on_page.append(job_id)
                except Exception as e:
                    print(f"⚠️ Error extracting job ID: {str(e)}")
                    continue
            
            print(f"\n📊 Found {len(job_ids_on_page)} new jobs to process on page {current_page}")
            
            # Process each job
            for idx, job_id in enumerate(job_ids_on_page, 1):
                try:
                    print(f"\n🔍 Processing job {idx}/{len(job_ids_on_page)} (ID: {job_id})")
                    
                    # Build URL based on collection
                    job_url = f"{base_url}?currentJobId={job_id}"
                    driver.get(job_url)
                    time.sleep(2)
                    
                    job_data = extract_job_details(driver)
                    if job_data:
                        analysis = analyze_job_relevance(job_data, cv_content)
                        job_data['analysis'] = analysis
                        
                        # Try to save the job if it's recommended
                        if analysis.get('should_apply', False):
                            try_save_job(driver)
                        
                        collection_jobs.append(job_data)
                        processed_job_ids.add(job_id)
                        
                except Exception as e:
                    print(f"❌ Error processing job {job_id}: {str(e)}")
                    continue
            
            # Try next page
            if not go_to_next_page(driver):
                print("🏁 No more pages in this collection")
                break
            
            current_page += 1
            time.sleep(3)
            
    except Exception as e:
        print(f"❌ Error processing collection: {str(e)}")
    
    return collection_jobs

def try_save_job(driver):
    """Attempt to save a job that should be applied to"""
    print("💾 Attempting to save recommended job...")
    try:
        # Try multiple selectors for the save button
        save_button_selectors = [
            "button.jobs-save-button",
            "button.artdeco-button--secondary[type='button']",
            "//button[contains(@class, 'jobs-save-button')]",
            "//button[.//span[contains(text(), 'Save')]]",
            "//button[contains(@class, 'artdeco-button--secondary') and .//span[contains(text(), 'Save')]]"
        ]
        
        for selector in save_button_selectors:
            try:
                # Wait for button with explicit wait
                if selector.startswith("//"):
                    save_button = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                else:
                    save_button = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                
                # Check if not already saved
                button_text = save_button.text.lower()
                if "saved" not in button_text and save_button.is_displayed() and save_button.is_enabled():
                    print(f"Found save button: {button_text}")
                    
                    # Scroll into view and click
                    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", save_button)
                    time.sleep(1)
                    
                    # Try multiple click methods
                    try:
                        save_button.click()
                    except:
                        try:
                            driver.execute_script("arguments[0].click();", save_button)
                        except:
                            ActionChains(driver).move_to_element(save_button).click().perform()
                    
                    print("✅ Successfully saved job")
                    time.sleep(1)
                    return True
                    
            except Exception:
                continue
        
        print("⚠️ Could not find or click save button")
        return False
        
    except Exception as e:
        print(f"❌ Error saving job: {str(e)}")
        return False

def extract_job_id(job_card):
    """Extract job ID from a job card using multiple methods"""
    try:
        # Method 1: Direct data attributes
        for attr in ['data-job-id', 'data-occludable-job-id']:
            job_id = job_card.get_attribute(attr)
            if job_id:
                return job_id
                
        # Method 2: From job link
        link_selectors = [
            "a[href*='/jobs/view/']",
            "a[href*='currentJobId=']",
            ".job-card-list__title"
        ]
        
        for selector in link_selectors:
            try:
                links = job_card.find_elements(By.CSS_SELECTOR, selector)
                for link in links:
                    href = link.get_attribute('href')
                    if href:
                        # Try /jobs/view/ format
                        if '/jobs/view/' in href:
                            job_id = href.split('/jobs/view/')[-1].split('/')[0]
                            if job_id.isdigit():
                                return job_id
                        
                        # Try currentJobId format
                        if 'currentJobId=' in href:
                            job_id = href.split('currentJobId=')[-1].split('&')[0]
                            if job_id.isdigit():
                                return job_id
            except:
                continue
                
        # Method 3: From parent elements
        parent = job_card
        for _ in range(3):  # Check up to 3 parent levels
            try:
                for attr in ['data-job-id', 'data-occludable-job-id']:
                    job_id = parent.get_attribute(attr)
                    if job_id:
                        return job_id
                parent = parent.find_element(By.XPATH, '..')
            except:
                break
        
        print(f"⚠️ Could not find job ID in card: {job_card.text[:100]}...")
        return None
        
    except Exception as e:
        print(f"❌ Error extracting job ID: {str(e)}")
        return None

def load_cv_text(cv_file_path):
    print("\n Loading CV content...")
    try:
        with open(cv_file_path, 'r') as f:
            cv_content = f.read()
        print(f"✅ CV loaded ({len(cv_content)} characters)")
        return cv_content
    except Exception as e:
        print(f"❌ Error loading CV: {str(e)}")
        return ""

def save_jobs(jobs, easy_apply=True):
    """Save jobs to appropriate JSON file based on Easy Apply status"""
    if not jobs:
        return True
        
    # Determine which file to use
    file_name = "easy_apply_jobs.json" if easy_apply else "external_jobs.json"
    file_path = os.path.join(SCRIPT_DIR, file_name)
    
    try:
        # Load existing jobs
        existing_jobs = []
        if os.path.exists(file_path):
            try:
                print(f"📚 Loading existing jobs from {file_name}")
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_jobs = json.load(f)
            except json.JSONDecodeError:
                print(f"⚠️ {file_name} was empty or corrupted, starting fresh")
                existing_jobs = []
        
        # Add new jobs that should be applied to
        new_jobs = [job for job in jobs if job.get('analysis', {}).get('should_apply', False)]
        if not new_jobs:
            print("ℹ️ No new jobs to save (none marked as 'should apply')")
            return True
            
        # Add only unique jobs
        existing_ids = {job.get('job_id') for job in existing_jobs}
        unique_new_jobs = [job for job in new_jobs if job.get('job_id') not in existing_ids]
        
        if not unique_new_jobs:
            print("ℹ️ No new unique jobs to save")
            return True
            
        # Combine and save
        all_jobs = existing_jobs + unique_new_jobs
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(file_path) or '.', exist_ok=True)
        
        # Save with error handling
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(all_jobs, f, indent=2, ensure_ascii=False)
            print(f"✅ Successfully saved {len(unique_new_jobs)} new jobs to {file_name}. Total: {len(all_jobs)}")
            return True
        except Exception as save_error:
            print(f"❌ Error writing to {file_name}: {str(save_error)}")
            # Try to save to a backup file
            backup_file = f"{file_name}.backup"
            with open(backup_file, 'w', encoding='utf-8') as f:
                json.dump(all_jobs, f, indent=2, ensure_ascii=False)
            print(f"✅ Saved backup to {backup_file}")
            return False
        
    except Exception as e:
        print(f"❌ Error in save_jobs: {str(e)}")
        return False

def analyze_job_relevance(job, cv_content):
    """Analyze job relevance with specific criteria"""
    print("\n🧠 Analyzing job relevance...")
    if not gemini_model:
        print("❌ Gemini client not initialized. Skipping analysis.")
        return {
            'scores': {}, 'should_apply': False, 'recommendation': 'Skipped analysis - Gemini API key missing.', 'benefits': [], 'total_score': 0, 'meaningfulness_score': 0, 'meaningfulness_justification': ''
        }
        
    try:
        prompt = f"""Analyze the following job posting based on the candidate's CV, preferences, and the criteria for a 'meaningful' job. Provide a structured analysis.

**Candidate Profile:**
- Role: Data Analyst/Data Scientist/BI Developer
- Experience: 4+ years
- Key Tech Stack: Python, SQL, MLflow, Random Forests, XGBoost, tabular data, geospatial data, cloud platforms (AWS/GCP/Azure), MLOps, ETL, data visualization (Tableau/PowerBI), machine learning frameworks (Scikit-learn, TensorFlow/Keras, PyTorch).
- Preferences: Remote preferred, but hybrid (up to 3 days in office) is acceptable (Barcelona-based). Minimum salary: 42k€/year (flexible for great opportunities). Values innovation, learning, professional growth, and an exciting work environment/mission.

**Job Details:**
Title: {job['title']}
Company: {job['company']}
Location: {job['location']}
Description (first 1500 chars): {job['description'][:1500]}...

**Analysis Instructions:**
1.  **Meaningfulness Score:** Assign a 'meaningfulness_score' from 0 to 10. This score should reflect how well the job aligns with the criteria for a 'meaningful' role defined below.
2.  **Meaningfulness Justification:** Provide a concise justification for the 'meaningfulness_score', extracting 2-3 key snippets or phrases from the job description that support your assessment.
3.  **Standard Scores:** Rate the following aspects on a scale of 0-10:
    *   **Skills Match:** How well do the candidate's skills align with the job requirements? (Consider existing stack: MLflow, Random Forests, XGBoost, SQL, geospatial data as a plus if the role deepens/builds on them).
    *   **Role Alignment:** How well does the role align with the candidate's general profile as a Data Analyst/Scientist/BI Developer?
    *   **Remote Work:** How well does the job's remote/hybrid setup align with candidate's preference?
    *   **Innovation Focus:** Does the role/company emphasize innovation, R&D, or building new things?
    *   **Compensation proxy:** (Infer if possible, or neutral 5/10 if not stated) Based on title/experience/location, does this role seem to align with a 42k€+ expectation?
4.  **Overall Final Score:** Based on all factors, provide a single 'Final Score' (0-10).
5.  **Salary Estimation:** Based on the job title, location, experience requirements, company size/type, and market rates, estimate a fair salary range in EUR (gross yearly). Be realistic and conservative for specific companies and locations:
    - **Glovo/delivery companies in Barcelona**: Typically 42-45k EUR max for data roles
    - **Tech startups in Barcelona**: Usually 45-55k EUR for mid-level data scientists  
    - **Large corporations in Barcelona**: 50-65k EUR for data scientist roles
    - **Remote European roles**: Can range 55-75k EUR depending on company
    - **Always consider company type, location constraints, and realistic market rates**
    Provide in format: "X-Y EUR" (e.g., "42000-45000 EUR").
6.  **Should Apply:** Based on the overall analysis, especially the meaningfulness score and final score, state YES or NO.
7.  **Recommendation:** Briefly explain your 'Should Apply' decision, highlighting the most critical factors.
8.  **Key Points:** List 3-4 bullet points covering the most important matches or mismatches from the entire analysis.

**Criteria for a 'Meaningful' Job (to inform Meaningfulness Score):**

**A. Strong Growth Opportunities (Weight: 60% of Meaningfulness Score):**
    *   **Learning & Development:** Explicit mention of learning new skills, tools ('opportunity to learn X,' 'work with cutting-edge Y'), or methodologies.
    *   **Career Progression:** Indication of mentorship programs, clear career progression paths, or professional development support.
    *   **Nature of Challenges:** Roles involving tackling complex challenges, R&D, innovation, or building new things (not just maintenance).
    *   **Modern Data Aspects:** Exposure to MLOps, advanced AI applications, new data engineering practices.

**B. Exciting & Engaging Work Environment/Mission (Weight: 40% of Meaningfulness Score):**
    *   **Company Mission & Impact:** Prioritize companies with a clear, positive mission (societal good, sustainability, innovation for impact, education, healthcare, solving significant global/local problems e.g., Barcelona/European focus). Keywords: 'make a difference,' 'social impact,' 'transforming [industry],' 'sustainable future.'
    *   **Company Culture & Values:** Evidence of a dynamic, collaborative, innovative, or empowering culture. Keywords: 'agile environment,' 'autonomy,' 'ownership,' 'data-driven culture,' 'passionate team,' 'learning culture.'
    *   **Nature of Work & Impact:** Roles where the Data Scientist/Analyst/BI Developer can have a clear impact, contribute to core products/strategy, or work on innovative projects. Keywords: 'building from scratch,' 'leading projects,' 'pioneering solutions,' working with unique/interesting datasets (including geospatial).
    *   **Technology & Innovation Stack:** Use of modern technologies, opportunities to innovate, or a forward-thinking approach to data.

**De-prioritize/Filter Out (These should result in a lower Meaningfulness Score):**
    *   Standard corporate roles with no discernible passion, mission, or positive impact (unless growth is exceptional).
    *   Generic consultancy roles (unless highly specialized in a valued impact area or offering exceptional growth).
    *   Roles focused primarily on routine reporting, basic maintenance of old systems, or lacking clear learning/development avenues.

**Output Format (Strictly Adhere to this):**

**Meaningfulness Score:** X/10
**Meaningfulness Justification:**
- Snippet 1: "..."
- Snippet 2: "..."
- Snippet 3: "..."

**Skills Match:** X/10
**Role Alignment:** X/10
**Remote Work:** X/10
**Innovation Focus:** X/10
**Compensation Proxy:** X/10
**Final Score:** X/10

**Salary Estimation:** X-Y EUR

**Should Apply:** [YES/NO]
**Recommendation:** [One or two sentences]

**Key Points:**
- [Point 1]
- [Point 2]
- [Point 3]
"""

        print("⚡ Sending request to Gemini API...")
        # Use Gemini API
        response = gemini_model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                # candidate_count=1, # Default is 1
                # stop_sequences=['\n\n\n'], # Optional: If needed
                # max_output_tokens=1500, # Adjusted for potentially longer justification
                temperature=0.2 # Slightly lower for more deterministic structured output
            )
        )

        # Ensure response.text is accessed correctly
        if response.parts:
             response_text = "".join(part.text for part in response.parts)
        else:
             # Handle cases where the response might be blocked or empty
             print("⚠️ Gemini API response was empty or blocked.")
             # You might want to inspect response.prompt_feedback here
             print(f"Prompt Feedback: {response.prompt_feedback}")
             response_text = "Error: No response from API or content blocked."


        print("\n📝 Raw API Response:")
        print("=" * 80)
        print(response_text)
        print("=" * 80)
        
        # Parse with improved regex
        scores = {}
        # Corrected regex: Use r'**' for markdown bold, not r'\\*\\*'
        score_pattern = r'\*\*(Skills Match|Role Alignment|Remote Work|Innovation Focus|Compensation Proxy|Final Score|Meaningfulness Score):\*\*\s*(\d+(?:\.\d+)?)/10'
        matches = re.finditer(score_pattern, response_text, re.IGNORECASE | re.MULTILINE)
        
        for match in matches:
            category, score_value = match.groups() 
            scores[category.lower().replace(' ', '_').replace('_focus','')] = float(score_value)

        # Extract Meaningfulness Justification
        justification = ""
        # Corrected regex and more robust lookahead for various next sections or end of text
        justification_pattern = r'\*\*Meaningfulness Justification:\*\*\s*(.*?)(?=\n\s*\*\*(?:Skills Match|Role Alignment|Remote Work|Innovation Focus|Compensation Proxy|Final Score):\*\*|$)'
        justification_match = re.search(justification_pattern, response_text, re.DOTALL | re.IGNORECASE)
        if justification_match:
            justification_text = justification_match.group(1).strip()
            snippet_matches = re.finditer(r'-\s*Snippet \d+:\s*"(.*?)"', justification_text, re.IGNORECASE)
            snippets = [sm.group(1) for sm in snippet_matches]
            if snippets:
                justification = "\n".join(snippets) 
            else: 
                justification = justification_text
        
        # Extract salary estimation
        salary_estimation = ""
        salary_pattern = r'\*\*Salary Estimation:\*\*\s*([0-9,\-\s]+EUR)'
        salary_match = re.search(salary_pattern, response_text, re.IGNORECASE)
        if salary_match:
            salary_estimation = salary_match.group(1).strip()
        
        # Extract should_apply decision
        should_apply = False
        # Corrected regex
        apply_pattern = r'\*\*Should Apply:\*\*\s*(YES|NO)'
        apply_match = re.search(apply_pattern, response_text, re.IGNORECASE)
        if apply_match:
            should_apply = apply_match.group(1).upper() == 'YES'
        
        # Extract recommendation
        # Corrected regex and more robust lookahead
        rec_pattern = r'\*\*Recommendation:\*\*\s*(.*?)(?=\n\s*\*\*Key Points:\*\*|$)'
        rec_match = re.search(rec_pattern, response_text, re.DOTALL | re.IGNORECASE)
        recommendation = rec_match.group(1).strip() if rec_match else ""
        
        # Extract Key Points
        key_points = []
        # Corrected regex and more robust lookahead to the end of the string
        key_points_pattern = r'\*\*Key Points:\*\*\s*(.*?)(?=\s*$)' 
        key_points_match = re.search(key_points_pattern, response_text, re.DOTALL | re.IGNORECASE)
        if key_points_match:
            key_points_text = key_points_match.group(1).strip() # Strip trailing/leading whitespace from the block
            key_points = [p.strip('- *').strip() for p in key_points_text.split('\n') if p.strip()]

        result = {
            'scores': scores,
            'meaningfulness_score': scores.get('meaningfulness_score', 0),
            'meaningfulness_justification': justification,
            'salary_estimation': salary_estimation,
            'should_apply': should_apply,
            'recommendation': recommendation,
            'key_points': key_points, 
            'total_score': scores.get('final_score', 0)
        }
        
        print(f"\n✅ Parsed analysis:")
        print(f"Meaningfulness Score: {result['meaningfulness_score']}/10")
        print(f"Justification: {result['meaningfulness_justification']}")
        print(f"Scores: {scores}")
        print(f"Should Apply: {'YES' if should_apply else 'NO'}")
        print(f"Total Score: {result['total_score']}/10")
        
        return result
        
    except Exception as e:
        print(f"❌ API error: {str(e)}")
        traceback.print_exc() # Add traceback for better debugging
        return {
            'scores': {},
            'should_apply': False,
            'recommendation': f'Error analyzing job: {str(e)}',
            'key_points': [], # Changed from benefits
            'total_score': 0,
            'meaningfulness_score': 0,
            'meaningfulness_justification': f'Error: {str(e)}'
        }

def parse_analysis(response):
    """Parse the structured analysis response"""
    print("\n📖 Parsing analysis response...")
    try:
        # Extract all scores using improved regex pattern
        scores = {}
        # Corrected regex: Use r'**' for markdown bold, not r'\\*\\*'
        score_pattern = r'\*\*(Skills Match|Role Alignment|Remote Work|Innovation Focus|Compensation Proxy|Final Score|Meaningfulness Score):\*\*\s*(\d+(?:\.\d+)?)/10'
        matches = re.finditer(score_pattern, response, re.IGNORECASE | re.MULTILINE)
        
        for match in matches:
            category, score_value = match.groups()
            scores[category.lower().replace(' ', '_').replace('_focus','')] = float(score_value)
        
        # Extract Meaningfulness Justification
        justification = ""
        # Corrected regex and more robust lookahead
        justification_pattern = r'\*\*Meaningfulness Justification:\*\*\s*(.*?)(?=\n\s*\*\*(?:Skills Match|Role Alignment|Remote Work|Innovation Focus|Compensation Proxy|Final Score):\*\*|$)'
        justification_match = re.search(justification_pattern, response, re.DOTALL | re.IGNORECASE)
        if justification_match:
            justification_text = justification_match.group(1).strip()
            snippet_matches = re.finditer(r'-\s*Snippet \d+:\s*"(.*?)"', justification_text, re.IGNORECASE)
            snippets = [sm.group(1) for sm in snippet_matches]
            if snippets:
                justification = "\n".join(snippets)
            else:
                justification = justification_text

        # Extract recommendation
        recommendation = ""
        # Corrected regex and more robust lookahead
        rec_pattern = r'\*\*Recommendation:\*\*\s*(.*?)(?=\n\s*\*\*Key Points:\*\*|$)'
        rec_match = re.search(rec_pattern, response, re.DOTALL | re.IGNORECASE)
        if rec_match:
            recommendation = rec_match.group(1).strip()
        
        # Extract Key Points
        key_points = []
        # Corrected regex and more robust lookahead
        key_points_pattern = r'\*\*Key Points:\*\*\s*(.*?)(?=\s*$)' # Capture until end of string, allowing trailing whitespace
        key_points_match = re.search(key_points_pattern, response, re.DOTALL | re.IGNORECASE)
        if key_points_match:
            key_points_text = key_points_match.group(1).strip() # Strip trailing/leading from block
            key_points = [p.strip('- *').strip() for p in key_points_text.split('\n') if p.strip()]
        
        # Determine should_apply from text
        should_apply = False
        # Corrected regex
        apply_pattern = r'\*\*Should Apply:\*\*\s*(YES|NO)'
        apply_match = re.search(apply_pattern, response, re.IGNORECASE)
        if apply_match:
            should_apply = apply_match.group(1).upper() == 'YES'

        result = {
            'scores': scores,
            'meaningfulness_score': scores.get('meaningfulness_score', 0),
            'meaningfulness_justification': justification,
            'should_apply': should_apply,
            'recommendation': recommendation,
            'key_points': key_points, 
            'total_score': scores.get('final_score', 0),
            'salary_estimation': salary_estimation
        }
        
        print(f"✅ Parsed analysis with final score: {result['total_score']}, Meaningfulness: {result['meaningfulness_score']}")
        return result
        
    except Exception as e:
        print(f"❌ Parse error: {str(e)}")
        traceback.print_exc()
        return {
            'scores': {},
            'recommendation': f'Error parsing analysis: {str(e)}',
            'key_points': [],
            'total_score': 0,
            'meaningfulness_score': 0,
            'meaningfulness_justification': f'Error parsing: {str(e)}',
            'should_apply': False
        }

def safe_click(driver, element, timeout=15):
    """Click element with multiple fallback methods"""
    try:
        WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(element))
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
        time.sleep(0.5)
        ActionChains(driver).move_to_element(element).click().perform()
        return True
    except Exception as e:
        print(f"Click failed: {str(e)}. Trying JavaScript click...")
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except:
            return False

def safe_find_element(driver, selectors, timeout=15):
    """Find element with multiple selector fallbacks"""
    if isinstance(selectors, str):
        selectors = [selectors]
        
    for selector in selectors:
        try:
            element = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
            print(f"✅ Found element with selector: {selector}")
            return element
        except:
            continue
            
    raise NoSuchElementException(f"Element not found with any selector: {selectors}")

def scroll_for_jobs(driver):
    """Enhanced scrolling with debugging and fallback methods"""
    print("\n🔄 Starting job scrolling process...")
    
    # Try to find the scrollable container
    scroll_container = find_scrollable_container(driver)
    
    if scroll_container:
        print("📜 Using container-based scrolling")
        return scroll_container_method(driver, scroll_container)
    else:
        print("📜 Falling back to manual scrolling method")
        return scroll_fallback_method(driver)

def scroll_and_get_jobs(driver, container):
    """Scroll container and get job cards"""
    print("\n📜 Scrolling for jobs...")
    last_height = driver.execute_script("return arguments[0].scrollHeight", container)
    scroll_attempts = 0
    max_attempts = 20
    
    while scroll_attempts < max_attempts:
        try:
            # Scroll down smoothly
            driver.execute_script("""
                arguments[0].scroll({
                    top: arguments[0].scrollHeight,
                    behavior: 'smooth'
                });
            """, container)
            
            # Wait for content to load
            time.sleep(random.uniform(1.0, 1.5))
            
            # Get new scroll height
            new_height = driver.execute_script("return arguments[0].scrollHeight", container)
            
            # Get visible job cards
            job_cards = container.find_elements(By.CSS_SELECTOR, "div.job-card-container")
            print(f"Found {len(job_cards)} visible jobs")
            
            # Check if we've loaded enough jobs
            if len(job_cards) >= 24:  # LinkedIn typically shows 25 jobs per page
                print("✅ Found full page of jobs")
                return job_cards
            
            # Check if we've reached the bottom
            if new_height == last_height:
                scroll_attempts += 1
                print(f"No new content loaded (attempt {scroll_attempts}/{max_attempts})")
            else:
                print("New content loaded")
                last_height = new_height
                scroll_attempts = 0
            
        except Exception as e:
            print(f"❌ Scroll error: {str(e)}")
            scroll_attempts += 1
    
    # Return whatever jobs we found
    return container.find_elements(By.CSS_SELECTOR, "div.job-card-container")

def save_analyzed_jobs(jobs):
    try:
        # Define file paths
        all_jobs_file = 'scraped_jobs.json'
        apply_jobs_file = 'apply_jobs.json'  # New file for jobs to apply to
        
        print(f"\n💾 Attempting to save {len(jobs)} jobs...")
        
        # Load existing jobs from both files
        existing_jobs = []
        apply_jobs = []
        
        if os.path.exists(all_jobs_file):
            with open(all_jobs_file, 'r', encoding='utf-8') as f:
                try:
                    existing_jobs = json.load(f)
                except json.JSONDecodeError:
                    existing_jobs = []
                    
        if os.path.exists(apply_jobs_file):
            with open(apply_jobs_file, 'r', encoding='utf-8') as f:
                try:
                    apply_jobs = json.load(f)
                except json.JSONDecodeError:
                    apply_jobs = []
        
        # Process new jobs
        existing_ids = {job.get('id') for job in existing_jobs}
        apply_ids = {job.get('id') for job in apply_jobs}
        
        for job in jobs:
            job_id = job.get('id')
            
            # Add to main jobs file if new
            if job_id not in existing_ids:
                existing_jobs.append(job)
                
            # Add to apply jobs file if should_apply is True
            if job.get('analysis', {}).get('should_apply') and job_id not in apply_ids:
                apply_jobs.append(job)
                print(f"✨ Job {job.get('title')} marked for application!")
        
        # Save both files
        with open(all_jobs_file, 'w', encoding='utf-8') as f:
            json.dump(existing_jobs, f, indent=2, ensure_ascii=False)
            
        with open(apply_jobs_file, 'w', encoding='utf-8') as f:
            json.dump(apply_jobs, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Saved {len(existing_jobs)} total jobs")
        print(f"✅ Saved {len(apply_jobs)} jobs to apply to")
        return True
        
    except Exception as e:
        print(f"❌ Error saving jobs: {str(e)}")
        return False

def go_to_next_page(driver):
    """Handle pagination with better debugging"""
    try:
        # First scroll to bottom smoothly
        driver.execute_script("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});")
        time.sleep(2)
        
        # Try to find the next button with multiple approaches
        next_button_locators = [
            (By.CSS_SELECTOR, "button.jobs-search-pagination__button--next"),
            (By.CSS_SELECTOR, "button[aria-label='Next']"),
            (By.CSS_SELECTOR, "button[aria-label='View next page']"),
            (By.XPATH, "//button[contains(@class, 'jobs-search-pagination__button--next')]"),
            (By.XPATH, "//button[contains(text(), 'Next')]"),
            (By.XPATH, "//button[@aria-label='Next' or @aria-label='View next page']")
        ]
        
        for locator in next_button_locators:
            try:
                next_button = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located(locator)
                )
                
                if next_button.is_displayed() and next_button.is_enabled():
                    print("\nNext button properties:")
                    print(f"Text: {next_button.text}")
                    print(f"Class: {next_button.get_attribute('class')}")
                    print(f"Aria-label: {next_button.get_attribute('aria-label')}")
                    
                    # Click the button
                    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", next_button)
                    time.sleep(2)
                    next_button.click()
                    print("✅ Successfully clicked next button")
                    return True
                    
            except Exception as e:
                continue
        
        return False
        
    except Exception as e:
        print(f"❌ Pagination error: {str(e)}")
        return False

def main():
    print("\n🚀 Starting LinkedIn job scraper...")
    options = ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--window-size=1400,900")
    
    try:
        print("1. Initializing Chrome driver...")
        driver = uc.Chrome(options=options)
        driver.maximize_window()
        
        print("2. Starting login sequence...")
        sign_in(driver)
        
        # Get job listings with the new format
        jobs = get_job_listings(driver)
        print(f"\n✅ Successfully processed {len(jobs)} jobs")
        
    except Exception as e:
        print(f"\n❌ Critical error: {str(e)}")
        if 'driver' in locals():
            driver.save_screenshot("error.png")
    finally:
        if 'driver' in locals():
            driver.quit()
        print("\n✅ Script completed")

if __name__ == "__main__":
    main()