#!/usr/bin/env python3
"""
Script to fix indentation issues in application_filler.py
"""

def fix_indentation_issues():
    """Fix specific indentation issues in the file"""
    
    # Read the file
    with open('application_filler.py', 'r') as f:
        lines = f.readlines()
    
    # Fix specific problematic lines
    fixed_lines = []
    
    for i, line in enumerate(lines):
        line_num = i + 1
        
        # Fix the specific indentation issues around lines 1773-1775
        if line_num == 1773 and 'time.sleep(3)' in line:
            fixed_lines.append('                    time.sleep(3)\n')
        elif line_num == 1774 and 'return True' in line:
            fixed_lines.append('                    return True\n')
        elif line_num == 1776 and 'continue' in line:
            fixed_lines.append('                    continue\n')
        
        # Fix the try/except issue around line 1810
        elif line_num == 1810 and 'except:' in line and len(line) - len(line.lstrip()) > 20:
            fixed_lines.append('            except:\n')
        
        # Fix the missing except clause
        elif 'except Exception as e:' in line and 'print(f"⚠️ Error detecting page errors:' in lines[i+1]:
            fixed_lines.append('    except Exception as e:\n')
        
        else:
            fixed_lines.append(line)
    
    # Write the fixed file
    with open('application_filler.py', 'w') as f:
        f.writelines(fixed_lines)
    
    print("✅ Fixed indentation issues in application_filler.py")

if __name__ == "__main__":
    fix_indentation_issues() 