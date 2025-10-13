#!/usr/bin/env python3
"""
JSONL to CSV Converter for Educational Latency Data

Converts latency.jsonl to CSV format with proper column mapping:
- Session ID <- session_id
- Timestamp <- timestamp (formatted as MM-DD-YYYY)  
- Turn # <- turn_id
- Type <- llm_calls[0].type
- Story ID <- story_id
- User Input <- user_input
- AI Output <- llm_calls[0].ai_output
- Latency <- llm_calls[0].duration

Output: CSV file in evals/ directory with date range in filename
"""

import json
import csv
import os
import re
from datetime import datetime
from typing import Dict, List, Any, Optional

def convert_timestamp_to_date(timestamp: float) -> str:
    """Convert Unix timestamp to MM-DD-YYYY format."""
    try:
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime('%m-%d-%Y')
    except (ValueError, OSError):
        return 'Invalid-Date'

def count_syllables(word: str) -> int:
    """Count syllables in a word using a simple heuristic."""
    word = word.lower().strip()
    if not word:
        return 0
    
    # Remove non-alphabetic characters
    word = re.sub(r'[^a-z]', '', word)
    if not word:
        return 0
    
    # Count vowel groups
    vowels = 'aeiouy'
    syllable_count = 0
    prev_was_vowel = False
    
    for char in word:
        is_vowel = char in vowels
        if is_vowel and not prev_was_vowel:
            syllable_count += 1
        prev_was_vowel = is_vowel
    
    # Handle silent 'e'
    if word.endswith('e') and syllable_count > 1:
        syllable_count -= 1
    
    # Every word has at least one syllable
    return max(1, syllable_count)

def calculate_readability_scores(text: str) -> tuple:
    """
    Calculate Flesch Reading Ease (FRE) and Flesch-Kincaid Grade Level (FKGL).
    
    FRE = 206.835 - (1.015 × ASL) - (84.6 × ASW)
    FKGL = (0.39 × ASL) + (11.8 × ASW) - 15.59
    
    Where:
    ASL = Average Sentence Length (total words ÷ total sentences)
    ASW = Average Syllables per Word (total syllables ÷ total words)
    """
    if not text or text == 'N/A':
        return 'N/A', 'N/A'
    
    # Clean text and remove markdown formatting
    clean_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # Remove **bold** formatting
    clean_text = re.sub(r'["""]', '"', clean_text)  # Normalize quotes
    
    # Count sentences (split by . ! ?)
    sentences = re.split(r'[.!?]+', clean_text)
    sentences = [s.strip() for s in sentences if s.strip()]
    sentence_count = len(sentences)
    
    if sentence_count == 0:
        return 'N/A', 'N/A'
    
    # Count words
    words = re.findall(r'\b[a-zA-Z]+\b', clean_text)
    word_count = len(words)
    
    if word_count == 0:
        return 'N/A', 'N/A'
    
    # Count syllables
    total_syllables = sum(count_syllables(word) for word in words)
    
    # Calculate averages
    asl = word_count / sentence_count  # Average Sentence Length
    asw = total_syllables / word_count  # Average Syllables per Word
    
    # Calculate FRE and FKGL
    fre = 206.835 - (1.015 * asl) - (84.6 * asw)
    fkgl = (0.39 * asl) + (11.8 * asw) - 15.59
    
    # Round to 2 decimal places
    return round(fre, 2), round(fkgl, 2)

def extract_llm_call_data(llm_calls: List[Dict[str, Any]]) -> tuple:
    """Extract type, ai_output, and duration from first llm_calls entry."""
    if not llm_calls or len(llm_calls) == 0:
        return 'N/A', 'N/A', 0.0
    
    first_call = llm_calls[0]
    llm_type = first_call.get('type', 'N/A')
    ai_output = first_call.get('ai_output', 'N/A')
    duration = first_call.get('duration', 0.0)
    
    return llm_type, ai_output, duration

def get_date_range_from_jsonl(input_file: str) -> tuple:
    """Get the date range from first and last timestamps in JSONL file."""
    timestamps = []
    
    try:
        with open(input_file, 'r', encoding='utf-8') as file:
            for line in file:
                line = line.strip()
                if line:
                    try:
                        data = json.loads(line)
                        timestamp = data.get('timestamp')
                        if timestamp:
                            timestamps.append(timestamp)
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        print(f"Error: Input file '{input_file}' not found.")
        return None, None
    
    if not timestamps:
        return None, None
    
    start_date = convert_timestamp_to_date(min(timestamps))
    end_date = convert_timestamp_to_date(max(timestamps))
    
    return start_date, end_date

def convert_jsonl_to_csv(input_file: str, output_dir: str) -> Optional[str]:
    """
    Convert JSONL file to CSV format.
    
    Args:
        input_file: Path to input JSONL file
        output_dir: Directory to save CSV file
        
    Returns:
        Path to created CSV file, or None if error
    """
    
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    # Determine date range for filename
    start_date, end_date = get_date_range_from_jsonl(input_file)
    if start_date and end_date:
        if start_date == end_date:
            filename = f"storywriting_eval_{start_date}.csv"
        else:
            filename = f"storywriting_eval_{start_date}_to_{end_date}.csv"
    else:
        filename = "storywriting_eval_unknown_dates.csv"
    
    output_file = os.path.join(output_dir, filename)
    
    # CSV column headers
    headers = [
        'Session ID',
        'Timestamp', 
        'Turn #',
        'Type',
        'Story ID',
        'User Input',
        'AI Output',
        'Latency (ms)',
        'Latency (s)',
        'FRE',
        'FKGL'
    ]
    
    rows_processed = 0
    rows_with_errors = 0
    
    try:
        with open(input_file, 'r', encoding='utf-8') as infile, \
             open(output_file, 'w', newline='', encoding='utf-8') as outfile:
            
            writer = csv.writer(outfile)
            writer.writerow(headers)
            
            for line_num, line in enumerate(infile, 1):
                line = line.strip()
                if not line:
                    continue
                    
                try:
                    data = json.loads(line)
                    
                    # Extract basic fields
                    session_id = data.get('session_id', 'N/A')
                    timestamp = data.get('timestamp')
                    turn_id = data.get('turn_id', 'N/A')
                    story_id = data.get('story_id', 'N/A')
                    user_input = data.get('user_input', 'N/A')
                    
                    # Convert timestamp
                    if timestamp:
                        formatted_timestamp = convert_timestamp_to_date(timestamp)
                    else:
                        formatted_timestamp = 'N/A'
                    
                    # Extract llm_calls data
                    llm_calls = data.get('llm_calls', [])
                    llm_type, ai_output, duration = extract_llm_call_data(llm_calls)
                    
                    # Clean up ai_output (remove newlines for CSV)
                    if ai_output != 'N/A':
                        ai_output = ai_output.replace('\n', ' ').replace('\r', '')
                    
                    # Convert duration to seconds (rounded to 2 decimal places)
                    duration_seconds = round(duration / 1000, 2) if isinstance(duration, (int, float)) else 'N/A'
                    
                    # Calculate readability scores
                    fre, fkgl = calculate_readability_scores(ai_output)
                    
                    # Write CSV row
                    row = [
                        session_id,
                        formatted_timestamp,
                        turn_id,
                        llm_type,
                        story_id,
                        user_input,
                        ai_output,
                        duration,  # Latency (ms)
                        duration_seconds,  # Latency (s)
                        fre,  # Flesch Reading Ease
                        fkgl  # Flesch-Kincaid Grade Level
                    ]
                    
                    writer.writerow(row)
                    rows_processed += 1
                    
                except json.JSONDecodeError as e:
                    print(f"Error parsing JSON on line {line_num}: {e}")
                    rows_with_errors += 1
                    continue
                except Exception as e:
                    print(f"Error processing line {line_num}: {e}")
                    rows_with_errors += 1
                    continue
    
    except FileNotFoundError:
        print(f"Error: Input file '{input_file}' not found.")
        return None
    except Exception as e:
        print(f"Error writing to output file: {e}")
        return None
    
    print(f"Conversion completed successfully!")
    print(f"Rows processed: {rows_processed}")
    if rows_with_errors > 0:
        print(f"Rows with errors: {rows_with_errors}")
    print(f"Output file: {output_file}")
    
    return output_file

def main():
    """Main function to run the conversion."""
    # File paths
    input_file = 'c:/Users/yuqia/Desktop/english-chatbot/backend/logs/latency.jsonl'
    output_dir = 'c:/Users/yuqia/Desktop/english-chatbot/backend/evals'
    
    print("Starting JSONL to CSV conversion...")
    print(f"Input file: {input_file}")
    print(f"Output directory: {output_dir}")
    
    result = convert_jsonl_to_csv(input_file, output_dir)
    
    if result:
        print(f"\nConversion successful! CSV file created: {result}")
    else:
        print("\nConversion failed. Please check the error messages above.")

if __name__ == "__main__":
    main()