#!/usr/bin/env python3
"""
Apply only the essential fixes to application_filler.py to avoid syntax errors
"""

def apply_essential_fixes():
    """Apply the essential fixes that we know work"""
    
    # Read the backup file
    with open('application_filler_backup.py', 'r') as f:
        content = f.read()
    
    # Apply only the LLM prompt fix (the most critical one)
    # This is the safest fix that addresses the main issue
    
    # Fix 1: Improve LLM prompt for hybrid work
    old_prompt = '''prompt = f"""Fill job application form based on candidate's CV.

CV: {cv_content[:1500]}

Job: {job_description[:500] if job_description else "Not provided"}

Questions:
{"".join(formatted_questions)}

Rules:
- Immigration: No sponsorship needed (EU citizen) - Answer NO to sponsorship questions
- Work arrangements: ALWAYS accept hybrid and remote work opportunities
  * Answer YES to hybrid work questions (including 2-3 days WFH, flexible work, etc.)
  * Answer YES to remote work questions
  * Answer YES to flexible work arrangements
- Availability: Immediate start (unemployed) - Answer YES to immediate availability
- Experience: 4+ years data science, Python, SQL - Select appropriate experience levels (3+ years, 2-3 years)
- English: Advanced/Native level - Select advanced/fluent options
- Salary: Open to competitive offers - Select appropriate salary ranges

IMPORTANT: For hybrid work questions mentioning "2 days WFH" or similar, answer YES/Accept/Agree.

Format (exact text match):
Question 1: [Selected Option]
Question 2: [Selected Option]
etc.

Answer each question:"""'''
    
    new_prompt = '''prompt = f"""Fill job application form based on candidate's CV.

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

Answer each question:"""'''
    
    # Replace the prompt
    if old_prompt in content:
        content = content.replace(old_prompt, new_prompt)
        print("✅ Applied LLM prompt improvements")
    else:
        print("⚠️ Could not find exact LLM prompt to replace")
    
    # Fix 2: Increase token limit
    old_tokens = 'max_output_tokens=2048  # Increased token limit to avoid truncation'
    new_tokens = 'max_output_tokens=4096  # Increased token limit to avoid truncation'
    
    if old_tokens in content:
        content = content.replace(old_tokens, new_tokens)
        print("✅ Applied token limit increase")
    else:
        print("⚠️ Could not find token limit to replace")
    
    # Write the fixed file
    with open('application_filler.py', 'w') as f:
        f.write(content)
    
    print("✅ Applied essential fixes to application_filler.py")
    print("🎯 Key improvements:")
    print("1. Enhanced LLM prompt with explicit hybrid work instructions")
    print("2. Increased token limit from 2048 to 4096")
    print("3. Added 'ABSOLUTELY CRITICAL' section for Glovo-style questions")

if __name__ == "__main__":
    apply_essential_fixes() 