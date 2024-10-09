import json
import os
import time
import random
import matplotlib.pyplot as plt
from dotenv import load_dotenv
from groq import Groq
from collections import deque
from pushbullet import Pushbullet

# Set this to True to only analyze already processed jobs
analyze_only = False

# Load environment variables
load_dotenv()

# Groq API settings
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.1-70b-versatile"
MAX_TOKENS = 131072  # Context window for llama-3.1-70b-versatile
TOKENS_PER_MINUTE = 5000
TOKENS_PER_HOUR = 131072
REQUESTS_PER_MINUTE = 30
REQUESTS_PER_HOUR = 1800

# Pushbullet settings
pb = Pushbullet(os.getenv("PUSHBULLET_API_KEY"))


# Update file paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT_FILE = os.path.join(SCRIPT_DIR, "job_scorer_checkpoint.json")
ANALYZED_JOBS_FILE = os.path.join(SCRIPT_DIR, "scraped_jobs.json")
SCORED_JOBS_FILE = os.path.join(SCRIPT_DIR, "scored_jobs.json")
CV_FILE_PATH = os.path.join(SCRIPT_DIR, "cv_text.txt")
GRAPH_FILE = os.path.join(SCRIPT_DIR, "job_score_distribution.png")

# Rate limiting tracking
class RateLimiter:
    def __init__(self):
        self.request_times = deque()
        self.token_usage = deque()
        self.hourly_request_times = deque()
        self.hourly_token_usage = deque()

    def add_request(self, tokens):
        current_time = time.time()
        self.request_times.append(current_time)
        self.token_usage.append(tokens)
        self.hourly_request_times.append(current_time)
        self.hourly_token_usage.append(tokens)

        # Remove old entries
        self.clean_old_entries()

    def clean_old_entries(self):
        current_time = time.time()
        while self.request_times and current_time - self.request_times[0] > 60:
            self.request_times.popleft()
            self.token_usage.popleft()
        while self.hourly_request_times and current_time - self.hourly_request_times[0] > 3600:
            self.hourly_request_times.popleft()
            self.hourly_token_usage.popleft()

    def check_limits(self):
        self.clean_old_entries()
        requests_last_minute = len(self.request_times)
        tokens_last_minute = sum(self.token_usage)
        requests_last_hour = len(self.hourly_request_times)
        tokens_last_hour = sum(self.hourly_token_usage)

        if requests_last_minute >= REQUESTS_PER_MINUTE:
            wait_time = 60 - (time.time() - self.request_times[0])
            print(f"Approaching RPM limit. Waiting for {wait_time:.2f} seconds.")
            time.sleep(wait_time)
        elif tokens_last_minute >= TOKENS_PER_MINUTE:
            wait_time = 60 - (time.time() - self.request_times[0])
            print(f"Approaching TPM limit. Waiting for {wait_time:.2f} seconds.")
            time.sleep(wait_time)
        elif requests_last_hour >= REQUESTS_PER_HOUR:
            wait_time = 3600 - (time.time() - self.hourly_request_times[0])
            print(f"Approaching RPH limit. Waiting for {wait_time:.2f} seconds.")
            time.sleep(wait_time)
        elif tokens_last_hour >= TOKENS_PER_HOUR:
            wait_time = 3600 - (time.time() - self.hourly_request_times[0])
            print(f"Approaching TPH limit. Waiting for {wait_time:.2f} seconds.")
            time.sleep(wait_time)

rate_limiter = RateLimiter()

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict) and "processed_jobs" in data and "last_processed_index" in data:
                    return data
                elif isinstance(data, list):
                    return {"processed_jobs": data, "last_processed_index": len(data)}
        except json.JSONDecodeError:
            print("Error reading checkpoint file. Starting with empty checkpoint.")
    return {"processed_jobs": [], "last_processed_index": 0}

def save_checkpoint(processed_jobs, last_processed_index):
    checkpoint_data = {
        "processed_jobs": processed_jobs,
        "last_processed_index": last_processed_index
    }
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(checkpoint_data, f, indent=2)

# Load checkpoint data
checkpoint = load_checkpoint()
processed_jobs = checkpoint["processed_jobs"]
last_processed_index = checkpoint["last_processed_index"]

print(f"Loaded {len(processed_jobs)} processed jobs from checkpoint.")

# Read CV text from file
try:
    with open(CV_FILE_PATH, "r") as f:
        cv_text = f.read()
except FileNotFoundError:
    print(f"Error: CV text file not found at {CV_FILE_PATH}. Please make sure the file exists at this location.")
    exit(1)

def estimate_tokens(text):
    return len(text) // 3

def batch_score_jobs(job_batch):
    prompt = f"""
    CV:
    {cv_text}

    For each job description below, rate the relevance of the job to the candidate's skills and experience on a scale of 1 to 10, where 1 is not relevant at all and 10 is extremely relevant. Provide a brief explanation for your rating in one sentence. Write a very compact cover letter (max 150 words) for this specific job based on the CV and job description.

    Your response for each job must follow this exact format:
    Job ID: [Job ID]
    Score: [Your score as a single number between 1 and 10]
    Cover Letter: [Your compact cover letter]

    Job Descriptions:
    """

    for job in job_batch:
        prompt += f"""
        Job ID: {job['job_id']}
        Title: {job['title']}
        Description: {job['description']}
        """

    estimated_tokens = estimate_tokens(prompt)
    rate_limiter.check_limits()

    max_retries = 5
    for attempt in range(max_retries):
        try:
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
            if "rate_limit_exceeded" in str(e):
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                print(f"Rate limit exceeded. Waiting for {wait_time:.2f} seconds before retrying.")
                time.sleep(wait_time)
            else:
                print(f"Error: API request failed - {str(e)}")
                return None
    
    print("Max retries reached. Skipping this batch.")
    return None

def parse_batch_response(response):
    job_scores = {}
    current_job_id = None
    for line in response.split('\n'):
        if line.startswith("Job ID:"):
            current_job_id = line.split(":")[1].strip()
            job_scores[current_job_id] = {"score": 0, "reasoning": ""}
        elif line.startswith("Score:") and current_job_id:
            job_scores[current_job_id]["score"] = int(line.split(":")[1].strip())
        elif line.startswith("Cover Letter:") and current_job_id:
            job_scores[current_job_id]["cover_letter"] = line.split(":", 1)[1].strip()
    return job_scores

def send_pushbullet_notification(job):
    title = f"New Relevant Job: {job['title']} (Score: {job['score']})"
    body = f"""
    Job Link: {job['link']}

    Cover Letter:
    {job['cover_letter']}
    """
    pb.push_note(title, body)

if not analyze_only:
    # Load the analyzed jobs
    with open(ANALYZED_JOBS_FILE, "r") as f:
        all_jobs = json.load(f)

    # Calculate tokens for CV and estimate tokens per job
    cv_tokens = estimate_tokens(cv_text)
    avg_job_tokens = sum(estimate_tokens(job['description']) for job in all_jobs) // len(all_jobs)

    # Calculate batch size more conservatively
    tokens_per_batch = min(TOKENS_PER_MINUTE, MAX_TOKENS) - cv_tokens - 2000
    batch_size = max(1, tokens_per_batch // (avg_job_tokens * 2))

    print(f"Processing jobs in batches of {batch_size}")
    print(f"Resuming from index {last_processed_index}")



    # Process jobs in batches
    try:
        for i in range(last_processed_index, len(all_jobs), batch_size):
            job_batch = all_jobs[i:i+batch_size]
            print(f"\nProcessing batch starting at index {i}")
            response = batch_score_jobs(job_batch)
            if response is None:
                print(f"Error occurred at index {i}. Saving checkpoint and exiting.")
                save_checkpoint(processed_jobs, i)
                exit(1)
            job_scores = parse_batch_response(response)
            
            for job in job_batch:
                if str(job['job_id']) in job_scores:
                    job['relevance_score'] = job_scores[str(job['job_id'])]['score']
                    #job['relevance_reasoning'] = job_scores[str(job['job_id'])]['reasoning']
                    processed_jobs.append(job)
                    print(f"Job ID: {job['job_id']}")
                    print(f"Title: {job['title']}")
                    print(f"Score: {job['relevance_score']}")
                    print(f"Cover Letter: {job_scores[str(job['job_id'])]['cover_letter']}")
                    #print(f"Reasoning: {job['relevance_reasoning']}")
                    print("---")

                    if job['relevance_score'] >= 7:
                        send_pushbullet_notification(job)
                else:
                    print(f"Warning: No score found for job ID {job['job_id']}")
            
            # Save checkpoint after each batch
            save_checkpoint(processed_jobs, i + batch_size)

    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Saving checkpoint and exiting.")
        save_checkpoint(processed_jobs, i)
        exit(0)

else:
    print("Analyze-only mode: Skipping job processing.")
    all_jobs = processed_jobs
# Analysis section
print("\nAnalyzing processed jobs...")

# Sort jobs by relevance score
sorted_jobs = sorted(all_jobs, key=lambda x: x.get('relevance_score', 0), reverse=True)

# Print relevant jobs (score >= 7)
print("\nMost relevant jobs (score >= 7):")
relevant_jobs = [job for job in sorted_jobs if job.get('relevance_score', 0) >= 7]
if relevant_jobs:
    for job in relevant_jobs:
        print(f"Title: {job['title']}")
        print(f"Score: {job.get('relevance_score', 'N/A')}")
        print(f"Reasoning: {job.get('relevance_reasoning', 'N/A')}")
        print(f"Link: {job.get('link', 'N/A')}")
        print("---")
else:
    print("No jobs with score 7 or higher found.")

# Save all jobs (processed and unprocessed) back to file
with open(SCORED_JOBS_FILE, "w") as f:
    json.dump(all_jobs, f, indent=2)

# Create a bar graph of job scores
scores = [job.get('relevance_score', 0) for job in all_jobs]
score_counts = {i: scores.count(i) for i in range(11)}  # 0 to 10 inclusive

plt.figure(figsize=(12, 6))
plt.bar(score_counts.keys(), score_counts.values())
plt.title('Distribution of Job Relevance Scores')
plt.xlabel('Relevance Score')
plt.ylabel('Number of Jobs')
plt.xticks(range(11))
for i, v in score_counts.items():
    plt.text(i, v, str(v), ha='center', va='bottom')
plt.savefig(GRAPH_FILE)
plt.close()

print(f"\nA bar graph of the job score distribution has been saved as '{GRAPH_FILE}'")

def cleanup_files():
    # Remove checkpoint file
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
        print("Checkpoint file removed.")
    
    # Clear contents of scored_jobs file
    with open(SCORED_JOBS_FILE, "w") as f:
        json.dump([], f)
    print("Scored jobs file cleared.")

if not analyze_only:
    cleanup_files()
    print("Processing completed successfully. Files cleaned up for the next run.")
else:
    print("Analysis of processed jobs completed. Files not cleaned up in analyze-only mode.")