# ╔══════════════════════════════════════════════════════════════╗
#   AI-to-AI SYNTHETIC DATA FACTORY  ──  v2.0 PRODUCTION GRADE
#   Teacher : Groq  (Llama-3.3-70B)   → generates hard problems
#   Student : Google Gemini 1.5 Pro   → full CoT solutions
#   Quality : Built-in validator, deduplicator, scorer
#   Output  : Google Drive  AI_Data/synthetic_data.jsonl
#   Author  : NovaMind AI
# ╚══════════════════════════════════════════════════════════════╝

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 0 ── Install packages
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import subprocess, sys

for pkg in ["groq", "google-generativeai", "tenacity"]:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 1 ── Imports
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import os, json, time, re, random, hashlib, logging
from datetime import datetime, timezone
from pathlib import Path
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import groq
import google.generativeai as genai

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 2 ── API KEYS  ← তোমার key এখানে বসাও
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GROQ_API_KEY   = "your_groq_api_key_here"     # groq.com → API Keys
GEMINI_API_KEY = "your_gemini_api_key_here"   # aistudio.google.com

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 3 ── Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GROQ_MODEL           = "llama-3.3-70b-versatile"
GEMINI_MODEL         = "gemini-1.5-pro"
QUESTIONS_PER_BATCH  = 25        # safe sweet-spot for JSON reliability
MAX_RETRIES          = 4
RETRY_WAIT_BASE      = 5         # seconds (exponential back-off base)
BATCH_COOLDOWN       = 12        # seconds between batches
PER_QUESTION_DELAY   = 1.2       # seconds between Gemini calls
MIN_REASONING_CHARS  = 800       # quality gate: reject short solutions
MIN_PROBLEM_WORDS    = 30        # quality gate: reject trivial problems
DRIVE_PATH           = "/content/drive/MyDrive/AI_Data/synthetic_data.jsonl"
LOCAL_BACKUP         = "/content/synthetic_data_backup.jsonl"
SEEN_HASHES_FILE     = "/content/seen_hashes.txt"   # deduplication store

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 4 ── Logging
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/content/factory.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("factory")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 5 ── Google Drive mount
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def mount_drive() -> bool:
    try:
        from google.colab import drive
        drive.mount("/content/drive", force_remount=False)
        Path(DRIVE_PATH).parent.mkdir(parents=True, exist_ok=True)
        log.info(f"✅ Google Drive mounted → {DRIVE_PATH}")
        return True
    except Exception as e:
        log.warning(f"⚠️  Drive mount failed: {e}  →  using local backup only")
        return False

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 6 ── API clients
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
groq_client  = groq.Groq(api_key=GROQ_API_KEY)
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(GEMINI_MODEL)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 7 ── Deduplication engine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class Deduplicator:
    """SHA-256 fingerprint of each problem — persisted across Colab restarts."""

    def __init__(self, path: str):
        self.path = path
        self.seen: set[str] = set()
        if Path(path).exists():
            with open(path, "r") as f:
                self.seen = set(line.strip() for line in f if line.strip())
        log.info(f"🔍 Deduplicator loaded {len(self.seen)} known hashes.")

    def _hash(self, text: str) -> str:
        return hashlib.sha256(text.lower().strip().encode()).hexdigest()[:16]

    def is_duplicate(self, text: str) -> bool:
        return self._hash(text) in self.seen

    def register(self, text: str):
        h = self._hash(text)
        self.seen.add(h)
        with open(self.path, "a") as f:
            f.write(h + "\n")

dedup = Deduplicator(SEEN_HASHES_FILE)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 8 ── Quality validator
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REQUIRED_SECTIONS = [
    "## UNDERSTANDING",
    "## KEY INSIGHTS",
    "## CHAIN-OF-THOUGHT REASONING",
    "## FORMAL SOLUTION",
    "## VERIFICATION",
    "## FINAL ANSWER",
]

def quality_score(record: dict) -> tuple[bool, float, str]:
    """
    Returns (passes: bool, score: float 0-1, reason: str).
    A record must score ≥ 0.75 to be saved.
    """
    prob      = record.get("instruction", "")
    reasoning = record.get("reasoning", "")
    output    = record.get("output", "")
    score     = 0.0
    issues    = []

    # ── Problem quality (30 pts) ──────────────────────────────
    words = len(prob.split())
    if words >= MIN_PROBLEM_WORDS:
        score += 0.20
    else:
        issues.append(f"problem too short ({words} words)")

    if any(c.isdigit() for c in prob):          # has numbers/formulas
        score += 0.05
    if "?" in prob or "prove" in prob.lower() or "find" in prob.lower():
        score += 0.05

    # ── Solution structure (40 pts) ───────────────────────────
    found_sections = sum(1 for s in REQUIRED_SECTIONS if s in reasoning)
    score += 0.40 * (found_sections / len(REQUIRED_SECTIONS))
    if found_sections < len(REQUIRED_SECTIONS):
        issues.append(f"missing {len(REQUIRED_SECTIONS)-found_sections} section(s)")

    # ── Solution depth (20 pts) ───────────────────────────────
    r_len = len(reasoning)
    if r_len >= MIN_REASONING_CHARS * 3:
        score += 0.20
    elif r_len >= MIN_REASONING_CHARS:
        score += 0.10
    else:
        issues.append(f"reasoning too short ({r_len} chars)")

    # ── Final answer present (10 pts) ─────────────────────────
    if output and len(output) > 20:
        score += 0.10
    else:
        issues.append("final answer missing or too short")

    passes = score >= 0.75
    reason = "; ".join(issues) if issues else "OK"
    return passes, round(score, 3), reason

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 9 ── TEACHER  (Groq → problem generation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TEACHER_SYSTEM = """You are an elite problem-setter for the world's most prestigious competitions:
  • International Mathematical Olympiad (IMO)
  • ACM-ICPC World Finals
  • MIT / Stanford / Caltech PhD qualifying exams
  • Putnam Mathematical Competition
  • Google Code Jam / Meta Hacker Cup Finals
  • Nobel-Prize-level scientific reasoning puzzles

STRICT RULES:
1. Every problem MUST require at least 10 non-trivial reasoning steps.
2. No problem may be solvable by simple formula lookup or Wikipedia search.
3. Each problem must be 100% self-contained (no external references).
4. Vary difficulty: 60% olympiad-hard, 30% PhD-hard, 10% research-frontier.
5. Mix domains every batch; never repeat a problem structure.
6. Domains allowed: Advanced Number Theory, Abstract Algebra, Real/Complex Analysis,
   Combinatorics, Graph Theory, Algorithmic Complexity (P vs NP level),
   Dynamic Programming (hard variants), Computational Geometry,
   Quantum Computing Theory, Formal Logic & Model Theory,
   Information & Coding Theory, Statistical Mechanics, Topology,
   Game Theory, Cryptography (theoretical).

OUTPUT FORMAT: Return ONLY a raw JSON array. Zero markdown. Zero preamble.
[
  {
    "id": 1,
    "category": "<exact domain>",
    "difficulty": "olympiad | phd | research",
    "tags": ["<tag1>", "<tag2>"],
    "problem": "<full self-contained problem statement, minimum 60 words>"
  },
  ...
]"""

CATEGORIES = [
    "Advanced Number Theory",
    "Abstract Algebra & Group Theory",
    "Real & Complex Analysis",
    "Topology & Differential Geometry",
    "Combinatorics & Generating Functions",
    "Graph Theory",
    "Algorithmic Complexity Theory",
    "Dynamic Programming (Hard)",
    "Computational Geometry",
    "Quantum Computing Theory",
    "Formal Logic & Model Theory",
    "Information & Coding Theory",
    "Statistical Mechanics & Thermodynamics",
    "Game Theory & Mechanism Design",
    "Theoretical Cryptography",
]

@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=RETRY_WAIT_BASE, min=RETRY_WAIT_BASE, max=60),
    retry=retry_if_exception_type(Exception),
    reraise=False,
)
def _call_groq(prompt: str) -> str:
    resp = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": TEACHER_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.97,
        max_tokens=7000,
    )
    return resp.choices[0].message.content.strip()

def teacher_generate(batch_num: int) -> list[dict]:
    cats = random.sample(CATEGORIES, k=min(5, len(CATEGORIES)))
    prompt = (
        f"Generate exactly {QUESTIONS_PER_BATCH} brutally hard, completely original problems. "
        f"This batch focus: {', '.join(cats)}. "
        f"Every problem must be unique — never repeat a structure from previous batches. "
        f"Batch #{batch_num}. Output ONLY the JSON array."
    )
    try:
        raw = _call_groq(prompt)
        # Remove accidental markdown fences
        raw = re.sub(r"^```[a-z]*\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$",       "", raw, flags=re.MULTILINE)
        raw = raw.strip()
        questions = json.loads(raw)
        if not isinstance(questions, list):
            raise ValueError("Expected a JSON array")
        log.info(f"  📚 Teacher → {len(questions)} problems generated")
        return questions
    except json.JSONDecodeError as e:
        log.warning(f"  ⚠️  Teacher JSON parse error: {e}")
        return []
    except Exception as e:
        log.warning(f"  ⚠️  Teacher failed: {e}")
        return []

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 10 ── STUDENT  (Gemini → Chain-of-Thought solution)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STUDENT_SYSTEM = """You are a Senior Research Scientist with world-class expertise across \
mathematics, theoretical computer science, physics, and formal logic — \
the equivalent of a tenured professor at MIT, Stanford, or the Institute for Advanced Study.

YOUR MISSION: Produce reasoning of the highest caliber — matching or exceeding \
Anthropic Claude's best outputs. Every answer must be flawlessly professional, \
completely error-free, and written as a senior researcher contributing to a top-tier journal.

NEVER respond like a generic chatbot. ALWAYS think like a PhD-level expert.
NEVER skip steps. NEVER approximate where exactness is required.
ALWAYS justify every logical leap. ALWAYS verify your answer independently.

YOU MUST FOLLOW THIS EXACT 6-SECTION STRUCTURE — no exceptions:

## UNDERSTANDING
Restate the problem precisely in your own words.
Identify: all given information, all constraints, what must be proven or found.
Flag any subtleties or edge cases that a naive reader might miss.

## KEY INSIGHTS
Number each non-obvious insight required to solve this problem.
Explain WHY each insight is necessary and non-trivial.
This section alone should demonstrate expert-level understanding.

## CHAIN-OF-THOUGHT REASONING
Full step-by-step derivation. Every step explicitly justified.
Show ALL intermediate calculations — no "it can be shown that" shortcuts.
Use sub-steps (1a, 1b, 1c…) for complex stages.
Reference theorems and lemmas by name when applicable.

## FORMAL SOLUTION
Complete, rigorous, publication-quality solution.
Include full proofs where required.
State every theorem, lemma, or algorithm used (with brief justification).
For algorithmic problems: include time and space complexity analysis.

## VERIFICATION
Verify correctness using an independent method.
Check all edge cases and boundary conditions.
For mathematical proofs: verify with a small concrete example.
For algorithms: trace through a test case step-by-step.

## FINAL ANSWER
One concise, definitive statement of the answer or conclusion.
For proofs: state "QED" with the proven proposition.
For algorithms: state the solution with its complexity.

FORMATTING RULES:
- Use LaTeX notation for all mathematics: $inline$ and $$display$$
- Use code blocks for any algorithms or pseudocode
- Be exhaustive — a high-quality response should be 1000-3000 words
"""

@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=RETRY_WAIT_BASE, min=RETRY_WAIT_BASE, max=60),
    retry=retry_if_exception_type(Exception),
    reraise=False,
)
def _call_gemini(problem: str) -> str:
    full_prompt = (
        STUDENT_SYSTEM
        + "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        + "PROBLEM TO SOLVE:\n\n"
        + problem
    )
    response = gemini_model.generate_content(
        full_prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0.25,
            max_output_tokens=4096,
        ),
    )
    return response.text.strip()

def student_solve(problem: str, q_id: int) -> dict | None:
    try:
        full_text = _call_gemini(problem)
        if not full_text:
            return None

        # Extract FINAL ANSWER section
        match = re.search(r"## FINAL ANSWER\s*\n([\s\S]+?)(?=\n## |\Z)", full_text)
        final_ans = match.group(1).strip() if match else full_text[-600:].strip()

        return {"reasoning": full_text, "output": final_ans}
    except Exception as e:
        log.warning(f"    ⚠️  Student failed on Q{q_id}: {e}")
        return None

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 11 ── Save (Drive primary + local backup always)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def save_record(record: dict, use_drive: bool):
    line = json.dumps(record, ensure_ascii=False) + "\n"
    if use_drive:
        try:
            with open(DRIVE_PATH, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            log.warning(f"    ⚠️  Drive write error: {e}")
    with open(LOCAL_BACKUP, "a", encoding="utf-8") as f:
        f.write(line)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 12 ── Stats tracker
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class Stats:
    def __init__(self):
        self.batches = self.generated = self.saved = 0
        self.skipped_dup = self.skipped_quality = 0
        self.t0 = datetime.now()

    def report(self):
        elapsed = datetime.now() - self.t0
        h, rem  = divmod(int(elapsed.total_seconds()), 3600)
        m, s    = divmod(rem, 60)
        rate    = self.saved / max(elapsed.total_seconds() / 3600, 0.01)
        print(
            f"\n{'═'*58}\n"
            f"  ⏱  Runtime        : {h:02d}h {m:02d}m {s:02d}s\n"
            f"  🔄 Batches         : {self.batches}\n"
            f"  ❓ Generated       : {self.generated}\n"
            f"  ✅ Saved (clean)   : {self.saved}\n"
            f"  🔁 Duplicates skip : {self.skipped_dup}\n"
            f"  ❌ Quality reject  : {self.skipped_quality}\n"
            f"  📊 Quality rate    : {self.saved/max(self.generated,1)*100:.1f}%\n"
            f"  🚀 Speed           : {rate:.1f} records/hour\n"
            f"{'═'*58}"
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 13 ── MAIN LOOP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║   🏭  AI-to-AI SYNTHETIC DATA FACTORY  v2.0          ║")
    print("║   Teacher : Groq  Llama-3.3-70B                      ║")
    print("║   Student : Google Gemini 1.5 Pro                    ║")
    print("║   Quality : Validator + Deduplicator + Scorer        ║")
    print("╚══════════════════════════════════════════════════════╝\n")

    use_drive = mount_drive()
    stats     = Stats()
    batch_num = 0

    while True:     # ← runs until Colab session ends
        batch_num += 1
        print(f"\n{'─'*58}")
        print(f"  🔄 BATCH #{batch_num}  |  {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'─'*58}")

        questions = teacher_generate(batch_num)
        if not questions:
            log.warning("⏭  Empty batch — retrying in 15s")
            time.sleep(15)
            continue

        stats.batches   += 1
        stats.generated += len(questions)

        for idx, q in enumerate(questions, 1):
            q_id   = q.get("id", idx)
            cat    = q.get("category", "Unknown")
            diff   = q.get("difficulty", "olympiad")
            tags   = q.get("tags", [])
            prob   = q.get("problem", "").strip()

            # ── Guard: empty problem ───────────────────────────
            if not prob or len(prob.split()) < 10:
                log.info(f"  ⏭  Q{q_id}: problem too short, skipping")
                stats.skipped_quality += 1
                continue

            # ── Guard: duplicate ───────────────────────────────
            if dedup.is_duplicate(prob):
                log.info(f"  🔁 Q{q_id}: duplicate detected, skipping")
                stats.skipped_dup += 1
                continue

            log.info(f"  🧠 Q{q_id}/{len(questions)} [{cat}] solving…")

            solution = student_solve(prob, q_id)
            if solution is None:
                stats.skipped_quality += 1
                continue

            # ── Build record ───────────────────────────────────
            record = {
                "id":          f"b{batch_num:04d}_q{q_id:04d}",
                "timestamp":   datetime.now(timezone.utc).isoformat(),
                "category":    cat,
                "difficulty":  diff,
                "tags":        tags,
                "instruction": prob,
                "reasoning":   solution["reasoning"],
                "output":      solution["output"],
            }

            # ── Quality gate ───────────────────────────────────
            passes, score, reason = quality_score(record)
            record["quality_score"] = score

            if not passes:
                log.warning(f"  ❌ Q{q_id} rejected (score={score}): {reason}")
                stats.skipped_quality += 1
                continue

            # ── Save ───────────────────────────────────────────
            save_record(record, use_drive)
            dedup.register(prob)
            stats.saved += 1
            log.info(
                f"  ✅ Q{q_id} saved | score={score} | "
                f"{len(solution['reasoning'])} chars"
            )

            time.sleep(PER_QUESTION_DELAY)

        stats.report()
        log.info(f"⏳ Batch cooldown {BATCH_COOLDOWN}s…")
        time.sleep(BATCH_COOLDOWN)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n🛑 Stopped by user. All data safely saved!")
