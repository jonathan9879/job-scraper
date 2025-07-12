# Job Scraper Project

## Current Project Status

This project is a job scraping and application automation tool primarily focused on LinkedIn and Seek job platforms. Here's a summary of the current components and functionality:

### Key Files and Components:
- **linkedin_scrapper.py**: Script for scraping job listings from LinkedIn.
- **seek_scraper.py**: Script for scraping job listings from Seek.
- **application_filler.py**: Likely handles filling out application forms (appears to be in development).
- **cv_text.txt**: Contains the user's CV text, used for matching and potentially auto-filling.
- **scraped_jobs.json**: Stores scraped job data.
- **easy_apply_jobs.json**: Stores jobs suitable for easy apply.
- **external_jobs.json**: Possibly stores jobs from external sources.
- **form_data.json**: Pre-filled form data for applications.
- **form_fields_db.json**: Database of common form fields.
- **job_scorer_checkpoint.json**: Checkpoint for job scoring model.
- **requirements.txt**: List of Python dependencies.

### Current Functionality:
- Scrapes job listings from LinkedIn and Seek.
- Scores jobs based on requirements (using some scoring mechanism, possibly LLM-based).
- Saves jobs that match specified requirements to JSON files.
- Basic setup for application filling, but currently only saves matching jobs without auto-applying.

The project is in a development state with some files untracked in Git and modifications pending commit.

## Next Steps

1. **Enhance Easy Apply Functionality**:
   - Implement automatic clicking of "Easy Apply" buttons.
   - Dynamically fill common fields (e.g., contact details) using pre-stored data from `form_data.json` or similar.
   - For non-standard questions (e.g., salary expectations, years of experience), integrate Gemini API to analyze the job description, CV (`cv_text.txt`), and question to generate responses.

2. **Integrate Trello Board**:
   - Create Trello cards for each applied job to track status.
   - Automate card creation when an application is submitted.

3. **Email Response Handling**:
   - Set up an email system with API (e.g., using Gmail API or similar) to monitor responses.
   - When a response is received, ping an endpoint to update the Trello card status automatically.
   - Deploy this on a cloud service like Google Cloud Run for continuous operation.

4. **Database Integration**:
   - Move job and application data storage to Supabase DB for centralized management.

5. **Expand to Normal Apply**:
   - Handle redirects to company websites.
   - Automate cookie acceptance, form filling, CV uploads, and navigation through multi-step application processes.

6. **Automation Improvements**:
   - Reduce manual confirmations where possible to enable more automated runs.

## Final Vision

The ultimate goal is a fully automated job application pipeline that:
- Scrapes jobs from multiple sources (LinkedIn, Seek, etc.).
- Scores and filters jobs based on user criteria.
- Automatically applies to jobs:
  - For Easy Apply: Fills forms intelligently using stored data and LLM (Gemini API) for complex questions.
  - For Normal Apply: Navigates external sites, handles cookies, uploads CV, fills forms, and submits.
- Centralizes tracking in a Trello board:
  - Creates cards for each application.
  - Updates statuses automatically via email response monitoring.
- Stores all data in a Supabase database for persistence and querying.
- Runs semi-automatically (with minimal manual intervention) on a cloud platform, providing live updates visible to others (e.g., via shared Trello board).

This will create a hands-off system where applications are submitted, tracked, and updated in real-time, with visibility for family members.

## Setup and Running
- Install dependencies: `pip install -r requirements.txt`
- Run scrapers: `python linkedin_scrapper.py` or `python seek_scraper.py`
- (Further instructions to be added as features are implemented)

## TODOs
- Commit untracked files and changes to Git.
- Add error handling and logging.
- Integrate APIs (Gemini, Trello, Supabase, Email). 