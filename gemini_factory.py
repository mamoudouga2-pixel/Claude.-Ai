# ╔══════════════════════════════════════════════════════════════╗
#   AI SYNTHETIC DATA FACTORY — GEMINI ONLY VERSION
#   Teacher : Gemini 1.5 Flash  (প্রশ্ন তৈরি)
#   Student : Gemini 1.5 Pro    (সমাধান)
#   Storage : Google Drive ✅
#   FREE   : ১৫০০ request/day — সম্পূর্ণ বিনামূল্যে!
# ╚══════════════════════════════════════════════════════════════╝

import subprocess, sys
for pkg in ["google-generativeai"]:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

# ━━━━ Drive Mount ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from google.colab import drive
import os, json, time, re, random, hashlib, logging
from datetime import datetime, timezone
from pathlib import Path
import google.generativeai as genai

print("📂 Google Drive mount হচ্ছে...")
drive.mount('/content/drive', force_remount=True)

DRIVE_FOLDER = "/content/drive/MyDrive/AI_Data"
os.makedirs(DRIVE_FOLDER, exist_ok=True)
DRIVE_PATH = f"{DRIVE_FOLDER}/synthetic_data.jsonl"
LOCAL_PATH = "/content/backup.jsonl"
HASH_FILE  = f"{DRIVE_FOLDER}/hashes.txt"

print(f"✅ Drive connected!")
print(f"📁 সেভ হবে: {DRIVE_PATH}")

# ━━━━ API KEY ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GEMINI_KEY = "your_gemini_key_here"  # ← এখানে Gemini key বসাও

genai.configure(api_key=GEMINI_KEY)

teacher_model = genai.GenerativeModel("gemini-1.5-flash")
student_model = genai.GenerativeModel("gemini-1.5-pro")

# ━━━━ Config ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
QUESTIONS_PER_BATCH = 15
MAX_RETRIES         = 3
BATCH_COOLDOWN      = 12
MIN_CHARS           = 600

# ━━━━ Logging ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"{DRIVE_FOLDER}/log.txt", encoding="utf-8"),
    ],
)
log = logging.getLogger("factory")

# ━━━━ Deduplication ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
seen = set()
if Path(HASH_FILE).exists():
    seen = set(open(HASH_FILE).read().splitlines())
log.info(f"🔍 {len(seen)} পুরনো hash লোড হয়েছে")

def is_dup(text):
    h = hashlib.sha256(text.lower().strip().encode()).hexdigest()[:16]
    return h in seen

def add_hash(text):
    h = hashlib.sha256(text.lower().strip().encode()).hexdigest()[:16]
    seen.add(h)
    with open(HASH_FILE, "a") as f:
        f.write(h + "\n")

# ━━━━ Save ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def save(record):
    line = json.dumps(record, ensure_ascii=False) + "\n"
    try:
        with open(DRIVE_PATH, "a", encoding="utf-8") as f:
            f.write(line)
        log.info("    💾 Google Drive-এ সেভ হয়েছে! ✅")
    except Exception as e:
        log.warning(f"    ⚠️ Drive error: {e}")
    with open(LOCAL_PATH, "a", encoding="utf-8") as f:
        f.write(line)

# ━━━━ Teacher Prompt ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TEACHER_PROMPT = """You are an elite problem-setter for the world's hardest competitions:
IMO, ACM-ICPC, MIT/Stanford PhD exams, Putnam, Google Code Jam Finals.

Generate EXACTLY {n} extremely difficult, 100% original problems.
Mix these domains: Number Theory, Abstract Algebra, Graph Theory, 
Algorithmic Complexity, Combinatorics, Quantum Computing Theory,
Formal Logic, Real Analysis, Computational Geometry, Game Theory.

STRICT RULES:
1. Every problem needs minimum 10 non-trivial reasoning steps
2. No problem solvable by simple formula or Wikipedia lookup
3. Every problem must be fully self-contained
4. Mix difficulty: 60% olympiad, 30% PhD, 10% research-frontier
5. Minimum 60 words per problem

Return ONLY a valid JSON array. No markdown. No explanation.
[
  {{"id":1,"category":"...","difficulty":"olympiad|phd|research","problem":"..."}}
]

Batch #{batch} — make all problems completely unique."""

# ━━━━ Student Prompt ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STUDENT_PROMPT = """You are a Senior Research Scientist equivalent to a tenured MIT/Stanford professor.
Your goal: produce reasoning matching Anthropic Claude's highest quality outputs.
Always use rigorous step-by-step logic. Be flawlessly professional and error-free.
Write like a senior researcher for a Nature or Science journal.

FOLLOW THIS EXACT 6-SECTION STRUCTURE:

## UNDERSTANDING
Restate the problem precisely. Identify all given information, constraints, and what must be proven or found. Flag any subtle edge cases.

## KEY INSIGHTS
Number each non-obvious insight required. Explain WHY each insight is necessary and non-trivial.

## CHAIN-OF-THOUGHT REASONING
Full step-by-step derivation. Every single step justified. Show ALL intermediate calculations. Use sub-steps (1a, 1b...) for complex stages. Reference theorems by name.

## FORMAL SOLUTION
Complete rigorous publication-quality solution. Full proofs where required. State every theorem and lemma used. Include time/space complexity for algorithms.

## VERIFICATION
Verify using an independent method. Check all edge cases. Trace through a concrete example.

## FINAL ANSWER
One concise definitive statement of the answer or conclusion.

Use LaTeX for all mathematics. Write 1000-3000 words. Never skip steps.

PROBLEM TO SOLVE:
{problem}"""

# ━━━━ Teacher ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def teacher_generate(batch_num):
    prompt = TEACHER_PROMPT.format(n=QUESTIONS_PER_BATCH, batch=batch_num)
    for attempt in range(MAX_RETRIES):
        try:
            resp = teacher_model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.95,
                    max_output_tokens=4000,
                ),
            )
            raw = resp.text.strip()
            raw = re.sub(r"^```[a-z]*\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"\s*```$",       "", raw, flags=re.MULTILINE)
            questions = json.loads(raw.strip())
            log.info(f"  📚 Teacher → {len(questions)} প্রশ্ন তৈরি!")
            return questions
        except json.JSONDecodeError:
            log.warning(f"  ⚠️ JSON error attempt {attempt+1}, retrying...")
            time.sleep(5)
        except Exception as e:
            log.warning(f"  ⚠️ Teacher attempt {attempt+1}: {e}")
            time.sleep(10 * (attempt + 1))
    return []

# ━━━━ Student ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def student_solve(problem, q_id):
    prompt = STUDENT_PROMPT.format(problem=problem)
    for attempt in range(MAX_RETRIES):
        try:
            resp = student_model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=4096,
                ),
            )
            text  = resp.text.strip()
            match = re.search(r"## FINAL ANSWER\s*\n([\s\S]+?)(?=\n## |\Z)", text)
            final = match.group(1).strip() if match else text[-400:]
            return {"reasoning": text, "output": final}
        except Exception as e:
            log.warning(f"    ⚠️ Student Q{q_id} attempt {attempt+1}: {e}")
            time.sleep(10 * (attempt + 1))
    return None

# ━━━━ Quality Check ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTIONS = [
    "## UNDERSTANDING", "## KEY INSIGHTS",
    "## CHAIN-OF-THOUGHT REASONING",
    "## FORMAL SOLUTION", "## VERIFICATION", "## FINAL ANSWER"
]

def quality_ok(record):
    prob = record.get("instruction", "")
    rsn  = record.get("reasoning", "")
    out  = record.get("output", "")
    score = 0.0
    if len(prob.split()) >= 30:        score += 0.20
    if any(c.isdigit() for c in prob): score += 0.05
    if "?" in prob or "prove" in prob.lower(): score += 0.05
    found = sum(1 for s in SECTIONS if s in rsn)
    score += 0.40 * (found / len(SECTIONS))
    if len(rsn) >= MIN_CHARS * 3:  score += 0.20
    elif len(rsn) >= MIN_CHARS:    score += 0.10
    if out and len(out) > 20:      score += 0.10
    return round(score, 3), score >= 0.65

# ━━━━ Stats ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class Stats:
    def __init__(self):
        self.batches = self.total = self.saved = self.rejected = 0
        self.t0 = datetime.now()
    def report(self):
        elapsed = datetime.now() - self.t0
        h, r = divmod(int(elapsed.total_seconds()), 3600)
        m, s = divmod(r, 60)
        rate = self.saved / max(elapsed.total_seconds()/3600, 0.01)
        print(f"\n{'═'*50}")
        print(f"  ⏱  সময়       : {h:02d}h {m:02d}m {s:02d}s")
        print(f"  🔄 Batch      : {self.batches}")
        print(f"  ❓ মোট       : {self.total}")
        print(f"  ✅ সেভ       : {self.saved}")
        print(f"  ❌ বাদ       : {self.rejected}")
        print(f"  🚀 Speed     : {rate:.1f} records/hour")
        print(f"  📁 Drive     : {DRIVE_PATH}")
        print(f"{'═'*50}\n")

# ━━━━ Main Loop ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    print("\n" + "╔"+"═"*48+"╗")
    print("║  🏭 AI SYNTHETIC DATA FACTORY — GEMINI ONLY  ║")
    print("║  Teacher : Gemini 1.5 Flash                  ║")
    print("║  Student : Gemini 1.5 Pro                    ║")
    print("║  Storage : Google Drive ✅                    ║")
    print("║  Cost    : সম্পূর্ণ ফ্রি! 🎉                ║")
    print("╚"+"═"*48+"╝\n")

    stats     = Stats()
    batch_num = 0

    while True:
        batch_num += 1
        print(f"\n{'─'*50}")
        print(f"  🔄 BATCH #{batch_num}  |  {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'─'*50}")

        questions = teacher_generate(batch_num)
        if not questions:
            log.warning("⏭ Empty batch — 20s পর আবার...")
            time.sleep(20)
            continue

        stats.batches += 1
        stats.total   += len(questions)

        for idx, q in enumerate(questions, 1):
            q_id = q.get("id", idx)
            cat  = q.get("category", "Unknown")
            diff = q.get("difficulty", "olympiad")
            prob = q.get("problem", "").strip()

            if not prob or len(prob.split()) < 15:
                stats.rejected += 1
                continue

            if is_dup(prob):
                log.info(f"  🔁 Q{q_id}: duplicate — skip")
                continue

            log.info(f"  🧠 Q{q_id}/{len(questions)} [{cat}] সমাধান হচ্ছে...")

            sol = student_solve(prob, q_id)
            if not sol:
                stats.rejected += 1
                continue

            record = {
                "id":            f"b{batch_num:04d}_q{q_id:04d}",
                "timestamp":     datetime.now(timezone.utc).isoformat(),
                "category":      cat,
                "difficulty":    diff,
                "instruction":   prob,
                "reasoning":     sol["reasoning"],
                "output":        sol["output"],
            }

            score, ok = quality_ok(record)
            record["quality_score"] = score

            if not ok:
                log.warning(f"  ❌ Q{q_id} quality fail (score={score})")
                stats.rejected += 1
                continue

            save(record)
            add_hash(prob)
            stats.saved += 1
            log.info(f"  ✅ Q{q_id} saved! score={score}")

            time.sleep(2)

        stats.report()
        time.sleep(BATCH_COOLDOWN)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 বন্ধ! সব ডেটা Drive-এ আছে!")
