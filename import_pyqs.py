import os
import re
import glob
import json
import sqlite3
import random

def parse_file(filepath):
    """
    Parses a Markdown file of CSIR NET questions, tracking syllabus units and sections.
    """
    with open(filepath, 'r', encoding='utf-8') as fp:
        lines = fp.readlines()
        
    fname = os.path.basename(filepath)
    # Extract 4-digit exam year from filename or first line
    year_match = re.search(r'(20\d\d)', fname)
    if not year_match and lines:
        year_match = re.search(r'(20\d\d)', lines[0])
    exam_year = year_match.group(1) if year_match else None

    current_unit = None
    blocks = []
    current_block_lines = []
    block_unit = None
    block_q_num = None

    for line in lines:
        line_stripped = line.strip()
        
        # Match syllabus unit / section headers
        unit_m = re.match(r'^(?:#{1,2})\s+((?:PART\s+[A-C]|UNIT\s+\d+|\d+\.\s+)[^(\n\r]+)', line_stripped, re.I)
        if unit_m and 'PART B & PART C' not in line_stripped.upper():
            current_unit = unit_m.group(1).strip()

        # Match question start headers
        q_m = re.match(r'^(?:###\s+Question\s+(\d+)|####\s+Sr\s+(\d+)|####\s+Q\.(\d+))(.*)$', line_stripped)
        if q_m and 'Distribution' not in line_stripped and 'Summary' not in line_stripped:
            if current_block_lines:
                blocks.append((current_block_lines, block_unit, block_q_num))
            current_block_lines = [line]
            block_unit = current_unit
            block_q_num = int(q_m.group(1) or q_m.group(2) or q_m.group(3))
        elif current_block_lines:
            current_block_lines.append(line)

    if current_block_lines:
        blocks.append((current_block_lines, block_unit, block_q_num))

    return fname, exam_year, blocks


def clean_opt(s):
    """
    Cleans an option string by removing Option IDs and any trailing status or metadata lines.
    """
    s = re.split(r'\n\s*[-*]', s)[0]
    s = re.sub(r'\s*\(Option ID:?\s*`?\d+`?\)', '', s)
    return s.strip()


def parse_block(lines, default_unit, q_num, year):
    """
    Parses an individual question block into the database schema fields.
    """
    text = ''.join(lines)
    header = lines[0].strip()

    # Part resolution: In CSIR NET, Q1-20 is Part A, Q21-70 is Part B, Q71-145 is Part C
    part = None
    p_m = re.search(r'Part\s*([ABC])\b', header, re.I)
    if p_m:
        part = p_m.group(1).upper()
    elif q_num is not None:
        if q_num <= 20:
            part = 'A'
        elif q_num <= 70:
            part = 'B'
        else:
            part = 'C'

    # Correct Option resolution
    correct_opt = None
    ans_m = re.search(r'(?:Correct Answer|Official (?:NTA )?(?:Final )?Answer|Key & Concept|Key):\*?\*?\s*`?([^\n`]+)', text, re.I)
    if ans_m:
        ans_raw = ans_m.group(1).strip()
        ans_clean = re.sub(r'[\*`_\[\]]', '', ans_raw).strip()
        opt_match = re.search(r'\bOption\s*([1-4ABCD])\b', ans_clean, re.I)
        mapping = {'1': 'A', '2': 'B', '3': 'C', '4': 'D', 'A': 'A', 'B': 'B', 'C': 'C', 'D': 'D'}
        if opt_match:
            correct_opt = mapping.get(opt_match.group(1).upper())
        else:
            leading_m = re.match(r'^\s*([1-4ABCD])\b', ans_clean, re.I)
            if leading_m:
                correct_opt = mapping.get(leading_m.group(1).upper())
            elif 'Option targeting A' in ans_clean:
                correct_opt = 'A'
            elif 'Option Drop' in ans_clean:
                correct_opt = None

    # Options parsing
    options_dict = None
    # 1. Pipe-separated options in a single line: 1. ... | 2. ... | 3. ... | 4. ...
    pipe_m = re.search(r'^[ \t]*(?:-\s*)?1\.\s+(.+?)\s*\|\s*2\.\s+(.+?)\s*\|\s*3\.\s+(.+?)\s*\|\s*4\.\s+([^\n\r]+)', text, re.MULTILINE)
    if pipe_m:
        o1 = clean_opt(pipe_m.group(1))
        o2 = clean_opt(pipe_m.group(2))
        o3 = clean_opt(pipe_m.group(3))
        o4 = clean_opt(pipe_m.group(4))
        options_dict = {'A': o1, 'B': o2, 'C': o3, 'D': o4}
    else:
        # 2. Multiline numbered options
        opt_hdr = re.search(r'\*\*Options:?\*\*', text, re.I)
        if opt_hdr:
            opt_sub = text[opt_hdr.end():]
        else:
            opt_sub = text

        stop_m = re.search(r'\n\s*(?:[-*]\s*)?(?:<details|\*\*Correct|\*\*Official|\*\*Key|\*\*Status|Status:|###|---)', opt_sub, re.I)
        if stop_m:
            opt_sub = opt_sub[:stop_m.start()]

        m_lines = re.findall(r'(?:^|\n)\s*(?:[-*]\s*)?(?:\((\d)\)|(\d)\.)\s*(.*?)(?=(?:\n\s*(?:[-*]\s*)?(?:\(\d\)|\d\.)|\Z))', opt_sub, re.DOTALL)
        if len(m_lines) >= 4:
            num_map = {}
            for num_tuple in m_lines:
                n = num_tuple[0] or num_tuple[1]
                t = clean_opt(num_tuple[2])
                num_map[n] = t
            if all(k in num_map for k in ['1', '2', '3', '4']):
                options_dict = {'A': num_map['1'], 'B': num_map['2'], 'C': num_map['3'], 'D': num_map['4']}

    # Question text parsing
    q_text = None
    q_hdr = re.search(r'(?:^|\n)\s*(?:-\s*)?\*\*Question:?\*\*\s*(.*)', text, re.DOTALL | re.I)
    end_patterns = [
        r'\n\s*\*\*Options:?\*\*',
        r'\n[ \t]*(?:-\s*)?1\.\s+.*?\|\s*2\.',
        r'\n\s*(?:[-*]\s*)?(?:\(1\)|1\.)\s+',
        r'\n\s*(?:[-*]\s*)?(?:<details|\*\*Correct|\*\*Official|\*\*Key|\*\*Status|Status:|###|---)'
    ]
    if q_hdr:
        raw_q = q_hdr.group(1)
        min_pos = len(raw_q)
        for ep in end_patterns:
            em = re.search(ep, raw_q, re.I)
            if em and em.start() < min_pos:
                min_pos = em.start()
        q_text = raw_q[:min_pos].strip()
    else:
        content_after_header = '\n'.join(lines[1:])
        min_pos = len(content_after_header)
        for ep in end_patterns:
            em = re.search(ep, content_after_header, re.I)
            if em and em.start() < min_pos:
                min_pos = em.start()
        q_text = content_after_header[:min_pos].strip()

    # Clean question text
    if q_text:
        q_text = re.sub(r'^(?:`Question Description:[^`]+`|\*\*Question Description:[^\*]+\*\*)\s*', '', q_text, flags=re.I).strip()
        q_text = re.sub(r'^(?:-\s*\*\*Question ID:[^\n]+\n|\*\*Question ID:[^\n]+\n)', '', q_text, flags=re.I).strip()
        q_text = q_text.strip()

    return {
        'question_text': q_text,
        'options': options_dict,
        'correct_option': correct_opt,
        'syllabus_unit': default_unit,
        'part': part,
        'exam_year': year
    }


def main():
    db_path = 'csir_net.db'
    raw_dir = 'raw pyqs' if os.path.exists('raw pyqs') else 'raw_pyqs'
    
    if not os.path.exists(raw_dir):
        print(f"Error: Directory '{raw_dir}' does not exist.")
        return

    files = sorted(glob.glob(os.path.join(raw_dir, '*.md')))
    print(f"Found {len(files)} files in '{raw_dir}/'")

    # Initialize SQLite database
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS pyqs")
    cur.execute("""
    CREATE TABLE pyqs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question_text TEXT UNIQUE,
        options_json TEXT,
        correct_option TEXT,
        syllabus_unit TEXT,
        part TEXT,
        exam_year TEXT
    )
    """)
    conn.commit()

    total_processed = 0
    total_imported = 0
    skipped_rows = []

    for fpath in files:
        fname, exam_year, blocks = parse_file(fpath)
        for b_lines, b_unit, b_q_num in blocks:
            total_processed += 1
            res = parse_block(b_lines, b_unit, b_q_num, exam_year)

            # Validation checks
            reasons = []
            if not res['question_text']:
                reasons.append("Missing question text")
            if not res['options'] or len(res['options']) != 4 or not all(res['options'].values()):
                reasons.append("Missing or incomplete 4 options")
            if not res['correct_option'] or res['correct_option'] not in ['A', 'B', 'C', 'D']:
                reasons.append("Missing or invalid correct option")

            if reasons:
                skipped_rows.append({
                    'file': fname,
                    'header': b_lines[0].strip(),
                    'reason': "; ".join(reasons)
                })
                continue

            options_json = json.dumps(res['options'], ensure_ascii=False)
            try:
                cur.execute("""
                INSERT INTO pyqs (question_text, options_json, correct_option, syllabus_unit, part, exam_year)
                VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    res['question_text'],
                    options_json,
                    res['correct_option'],
                    res['syllabus_unit'],
                    res['part'],
                    res['exam_year']
                ))
                total_imported += 1
            except sqlite3.IntegrityError as e:
                skipped_rows.append({
                    'file': fname,
                    'header': b_lines[0].strip(),
                    'reason': f"IntegrityError: Duplicate question text ({e})"
                })

    conn.commit()

    print("\n" + "="*50)
    print("IMPORT SUMMARY")
    print("="*50)
    print(f"Total Question Blocks Processed : {total_processed}")
    print(f"Total Rows Imported Successfully: {total_imported}")
    print(f"Total Skipped / Malformed Rows  : {len(skipped_rows)}")

    if skipped_rows:
        print("\nBreakdown of Skipped Rows:")
        reasons_summary = {}
        for s in skipped_rows:
            reasons_summary[s['reason']] = reasons_summary.get(s['reason'], 0) + 1
        for r, cnt in reasons_summary.items():
            print(f"  - {r}: {cnt}")

    # Fetch 5 random sample rows
    print("\n" + "="*50)
    print("5 RANDOM SAMPLE ROWS FOR VERIFICATION")
    print("="*50)
    cur.execute("""
    SELECT id, exam_year, part, syllabus_unit, correct_option, options_json, question_text
    FROM pyqs
    ORDER BY RANDOM()
    LIMIT 5
    """)
    samples = cur.fetchall()

    for idx, row in enumerate(samples, 1):
        row_id, year, part, unit, ans, opts_str, q_txt = row
        opts = json.loads(opts_str)
        print(f"\n--- [Sample {idx} | ID: {row_id}] ---")
        print(f"Exam Year    : {year}")
        print(f"Part         : {part}")
        print(f"Syllabus Unit: {unit}")
        print(f"Correct Key  : {ans}")
        print(f"Options JSON : {json.dumps(opts, indent=2, ensure_ascii=False)}")
        print(f"Question Text:\n{q_txt}")
        print("-" * 50)

    conn.close()

if __name__ == '__main__':
    main()
