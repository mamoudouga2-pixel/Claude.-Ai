# ╔══════════════════════════════════════════════════════════════╗
#   SYNTHETIC DATA FACTORY - FINAL VERSION
#   Teacher : DeepSeek   |   Student : Google Gemini
#   Storage : Google Drive (PRIMARY) + Local (BACKUP)
# ╚══════════════════════════════════════════════════════════════╝

# ━━━━ STEP 1: Install ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import subprocess, sys
for pkg in ["openai", "google-generativeai", "tenacity"]:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

# ━━━━ STEP 2: Google Drive Mount ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from google.colab import drive
import os, json, time, re, random, hashlib, logging
from datetime import datetime, timezone
from pathlib import Path
from openai import OpenAI
import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_exponential

# Drive mount - এটাই সবচেয়ে গুরুত্বপূর্ণ
print("📂 Google Drive mount করা হচ্ছে...")
drive.mount('/content/drive', force_remount=True)

# Drive folder তৈরি করো
DRIVE_FOLDER = "/content/drive/MyDrive/AI_Data"
os.makedirs(DRIVE_FOLDER, exist_ok=True)
DRIVE_PATH   = f"{DRIVE_FOLDER}/synthetic_data.jsonl"
LOCAL_PATH   = "/content/synthetic_data_backup.jsonl"

print(f"✅ Google Drive connected!")
print(f"📁 ডেটা যাবে: {DRIVE_PATH}")

# ━━━━ STEP 3: API KEYS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DEEPSEEK_KEY = "your_deepseek_key_here"   # ← এখানে DeepSeek key
GEMINI_KEY   = "your_gemini_key_here"      # ← এখানে Gemini key

# ━━━━ STEP 4: Config ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
QUESTIONS_PER_BATCH = 20
MAX_RETRIES         = 3
BATCH_COOLDOWN      = 10
MIN_REASONING_CHARS = 800

# ━━━━ STEP 5: Logging ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"{DRIVE_FOLDER}/factory.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("factory")

# ━━━━ STEP 6: API Clients ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
deepseek = OpenAI(
    api_key=DEEPSEEK_KEY,
    base_url="https://api.deepseek.com"
)
genai.configure(api_key=GEMINI_KEY)
gemini = genai.GenerativeModel("gemini-1.5-pro")

# ━━━━ STEP 7: Deduplication ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HASH_FILE = f"{DRIVE_FOLDER}/seen_hashes.txt"
seen_hashes = set()
if Path(HASH_FILE).exists():
    seen_hashes = set(open(HASH_FILE).read().splitlines())
log.info(f"🔍 {len(seen_hashes)} টা পুরনো hash লোড হয়েছে")

def is_duplicate(text):
    h = hashlib.sha256(text.lower().strip().encode()).hexdigest()[:16]
    return h in seen_hashes

def register_hash(text):
    h = hashlib.sha256(text.lower().strip().encode()).hexdigest()[:16]
    seen_hashes.add(h)
    with open(HASH_FILE, "a") as f:
        f.write(h + "\n")

# ━━━━ STEP 8: Quality Check ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTIONS = [
    "## UNDERSTANDING", "## KEY INSIGHTS",
    "## CHAIN-OF-THOUGHT REASONING",
    "## FORMAL SOLUTION", "## VERIFICATION", "## FINAL ANSWER"
]

def quality_check(record):
    score = 0.0
    prob  = record.get("instruction", "")
    rsn   = record.get("reasoning", "")
    out   = record.get("output", "")

    if len(prob.split()) >= 30:       score += 0.20
    if any(c.isdigit() for c in prob): score += 0.05
    if "?" in prob or "prove" in prob.lower(): score += 0.05

    found = sum(1 for s in SECTIONS if s in rsn)
    score += 0.40 * (found / len(SECTIONS))

    if len(rsn) >= MIN_REASONING_CHARS * 3: score += 0.20
    elif len(rsn) >= MIN_REASONING_CHARS:   score += 0.10

    if out and len(out) > 20: score += 0.10

    return round(score, 3), score >= 0.70

# ━━━━ STEP 9: Save Function ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def save_record(record):
    line = json.dumps(record, ensure_ascii=False) + "\n"

    # PRIMARY: Google Drive
    try:
        with open(DRIVE_PATH, "a", encoding="utf-8") as f:
            f.write(line)
        log.info(f"    💾 Google Drive-এ সেভ হয়েছে!")
    except Exception as e:
        log.warning(f"    ⚠️ Drive error: {e}")

    # BACKUP: Local
    with open(LOCAL_PATH, "a", encoding="utf-8") as f:
        f.write(line)

# ━━━━ STEP 10: Teacher (DeepSeek) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TEACHER_PROMPT = """You are an elite problem-setter for IMO, ACM-ICPC, MIT/Stanford PhD exams.
Generate EXACTLY {n} extremely hard, original problems.
Mix: Number Theory, Abstract Algebra, Graph Theory, Algorithms, 
     Quantum Computing, Formal Logic, Combinatorics, Analysis.

RULES:
- Each problem needs 10+ reasoning steps minimum
- Must be 100% self-contained
- No trivial problems
- Batch #{batch}

Return ONLY a raw JSON array, zero markdown:
[{{"id":1,"category":"...","difficulty":"olympiad|phd|research","problem":"..."}}]"""

def teacher_generate(batch_num):
    for attempt in range(MAX_RETRIES):
        try:
            resp = deepseek.chat.completions.create(
                model="deepseek-chat",
                messages=[{
                    "role": "user",
                    "content": TEACHER_PROMPT.format(
                        n=QUESTIONS_PER_BATCH, batch=batch_num
                    )
                }],
                temperature=0.95,
                max_tokens=6000,
            )
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r"^```[a-z]*\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
            questions = json.loads(raw.strip())
            log.info(f"  📚 Teacher → {len(questions)} প্রশ্ন তৈরি!")
            return questions
        except Exception as e:
            log.warning(f"  ⚠️ Teacher attempt {attempt+1}: {e}")
            time.sleep(5 * (attempt + 1))
    return []

# ━━━━ STEP 11: Student (Gemini) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STUDENT_PROMPT = """You are a Senior Research Scientist — equivalent to MIT/Stanford professor.
Your goal: produce reasoning matching Anthropic Claude's highest quality.
ALWAYS use rigorous step-by-step logic. Be flawlessly professional and error-free.
Write like a senior researcher for a top-tier journal.

FOLLOW THIS EXACT STRUCTURE:

## UNDERSTANDING
[Restate precisely. Identify all given info, constraints, what must be proven.]

## KEY INSIGHTS  
[Number each non-obvious insight. Explain WHY each is necessary.]

## CHAIN-OF-THOUGHT REASONING
[Full step-by-step. Every step justified. Show ALL calculations. Use sub-steps 1a,1b...]

## FORMAL SOLUTION
[Complete rigorous solution. Full proofs. State all theorems used. Include complexity analysis.]

## VERIFICATION
[Independent verification. Check edge cases. Trace through example.]

## FINAL ANSWER
[One concise definitive answer/conclusion.]

Use LaTeX for math. Be exhaustive (1000-3000 words)."""

def student_solve(problem, q_id):
    for attempt in range(MAX_RETRIES):
        try:
            full_prompt = STUDENT_PROMPT + f"\n\nPROBLEM:\n{problem}"
            response = gemini.generate_content(
                full_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=4096,
                ),
            )
            text = response.text.strip()
            match = re.search(r"## FINAL ANSWER\s*\n([\s\S]+?)(?=\n## |\Z)", text)
            final = match.group(1).strip() if match else text[-500:]
            return {"reasoning": text, "output": final}
        except Exception as e:
            log.warning(f"    ⚠️ Student Q{q_id} attempt {attempt+1}: {e}")
            time.sleep(5 * (attempt + 1))
    return None

# ━━━━ STEP 12: Stats ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class Stats:
    def __init__(self):
        self.batches = self.generated = self.saved = 0
        self.rejected = self.duplicates = 0
        self.t0 = datetime.now()

    def report(self):
        elapsed = datetime.now() - self.t0
        h, rem = divmod(int(elapsed.total_seconds()), 3600)
        m, s   = divmod(rem, 60)
        rate   = self.saved / max(elapsed.total_seconds()/3600, 0.01)
        print(f"\n{'═'*55}")
        print(f"  ⏱  সময়         : {h:02d}h {m:02d}m {s:02d}s")
        print(f"  🔄 Batch        : {self.batches}")
        print(f"  ❓ মোট প্রশ্ন   : {self.generated}")
        print(f"  ✅ সেভ হয়েছে   : {self.saved}")
        print(f"  ❌ বাদ দেওয়া   : {self.rejected}")
        print(f"  🔁 Duplicate    : {self.duplicates}")
        print(f"  🚀 Speed        : {rate:.1f} records/hour")
        print(f"  📁 Drive path   : {DRIVE_PATH}")
        print(f"{'═'*55}\n")

# ━━━━ STEP 13: MAIN LOOP ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    print("\n" + "╔"+"═"*53+"╗")
    print("║   🏭  AI SYNTHETIC DATA FACTORY - FINAL VERSION    ║")
    print("║   Teacher : DeepSeek-Chat                          ║")
    print("║   Student : Google Gemini 1.5 Pro                  ║")
    print("║   Storage : Google Drive ✅                         ║")
    print("╚"+"═"*53+"╝\n")

    # Drive check
    if os.path.exists(DRIVE_FOLDER):
        print(f"✅ Google Drive ঠিকঠাক connected!")
        print(f"📁 ডেটা যাবে: {DRIVE_PATH}\n")
    else:
        print("❌ Drive connect হয়নি! থামছি...")
        return

    stats     = Stats()
    batch_num = 0

    while True:
        batch_num += 1
        print(f"\n{'─'*55}")
        print(f"  🔄 BATCH #{batch_num}  |  {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'─'*55}")

        questions = teacher_generate(batch_num)
        if not questions:
            log.warning("⏭ Empty batch — 15s পর আবার চেষ্টা করছে...")
            time.sleep(15)
            continue

        stats.batches   += 1
        stats.generated += len(questions)

        for idx, q in enumerate(questions, 1):
            q_id = q.get("id", idx)
            cat  = q.get("category", "Unknown")
            diff = q.get("difficulty", "olympiad")
            prob = q.get("problem", "").strip()

            if not prob or len(prob.split()) < 15:
                stats.rejected += 1
                continue

            if is_duplicate(prob):
                log.info(f"  🔁 Q{q_id}: duplicate — skip")
                stats.duplicates += 1
                continue

            log.info(f"  🧠 Q{q_id}/{len(questions)} [{cat}] সমাধান হচ্ছে...")

            solution = student_solve(prob, q_id)
            if not solution:
                stats.rejected += 1
                continue

            record = {
                "id":            f"b{batch_num:04d}_q{q_id:04d}",
                "timestamp":     datetime.now(timezone.utc).isoformat(),
                "category":      cat,
                "difficulty":    diff,
                "instruction":   prob,
                "reasoning":     solution["reasoning"],
                "output":        solution["output"],
            }

            score, passes = quality_check(record)
            record["quality_score"] = score

            if not passes:
                log.warning(f"  ❌ Q{q_id} quality fail (score={score})")
                stats.rejected += 1
                continue

            save_record(record)
            register_hash(prob)
            stats.saved += 1
            log.info(f"  ✅ Q{q_id} saved! score={score} | {len(solution['reasoning'])} chars")

            time.sleep(1.5)

        stats.report()
        log.info(f"⏳ {BATCH_COOLDOWN}s cooldown...")
        time.sleep(BATCH_COOLDOWN)

# ━━━━ RUN ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n🛑 বন্ধ করা হয়েছে। সব ডেটা Drive-এ সেভ আছে!")
