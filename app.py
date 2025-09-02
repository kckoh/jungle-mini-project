# app.py
from celery import Celery
from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash
from pymongo import MongoClient
import math
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps
import os
import re
import time
import unicodedata
import requests
from bs4 import BeautifulSoup
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")


leetcode_session = os.getenv("LEETCODE_SESSION")
leetcode_csrf = os.getenv("LEETCODE_CSRF")

# Celery 설정
app.config.update(
    CELERY_BROKER_URL=os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0"),
    CELERY_RESULT_BACKEND=os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
)
celery = Celery(
    app.import_name,
    broker=app.config["CELERY_BROKER_URL"],
    backend=app.config["CELERY_RESULT_BACKEND"],
)
# Flask의 설정값 전체를 Celery config로도 반영
celery.conf.update(app.config)

# MongoDB
client = MongoClient(os.environ.get("MONGO_URI"))
db = client.get_database()
users_col = db.get_collection("users")

# Auth guard toggle (default: disabled for local dev)
AUTH_GUARD_ENABLED = os.environ.get("AUTH_GUARD_ENABLED", "false").lower() == "true"

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not AUTH_GUARD_ENABLED:
            return view(*args, **kwargs)
        if not session.get("user_id"):
            nxt = request.path
            return redirect(url_for("login", next=nxt))
        return view(*args, **kwargs)
    return wrapped

# UI: 메인 페이지
@app.get("/")
def index():
    return render_template("index.html")

# health check API
@app.get("/api/health")
def health():
    return jsonify(ok=True)

@app.get("/db/ping")
def db_ping():
    try:
        db.command("ping")
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500



@app.route("/problems")
@login_required
def problems():
    # TODO: 실제 DB 연동 후 목록 가져오기 (데모 데이터)
    items_all = [
        {
            "id": 1,
            "title": "구간 합 구하기",
            "body": "누적 합으로 구간 합을 효율적으로...",
        "created_at": "방금 전",
        "created_ts": 3,
            "problem_ref": "BOJ 11659",
            "tags": ["prefix-sum", "array"],
            "code": "def prefix_sum(arr):\n    ...",
        },
        {
            "id": 2,
            "title": "두 수의 합",
            "body": "해시를 사용하여 O(N)으로 탐색...",
        "created_at": "1시간 전",
        "created_ts": 2,
            "problem_ref": "LeetCode 1",
            "tags": ["hash", "two-pointer"],
            # code 없음 → 첨삭 완료 배지 X
        },
        {
            "id": 3,
            "title": "배낭 문제",
            "body": "DP로 0/1 Knapsack을 해결...",
        "created_at": "어제",
        "created_ts": 1,
            "problem_ref": "BOJ 12865",
            "tags": ["dp", "bf"],
            "code": "// knapsack implementation",
        },
    ]
    # 필터링 대상 목록 (얕은 복사)
    items = list(items_all)
    # 서버 측 필터링: 다중 필드, 태그 멀티선택, 페이지네이션
    # fields: 다중 선택(예: fields=title&fields=body). 이전 호환(field=title)도 지원
    fields = [f.strip() for f in request.args.getlist("fields") if f.strip()]
    legacy_field = (request.args.get("field") or "").strip()
    if legacy_field and legacy_field not in fields:
        fields.append(legacy_field)
    q = (request.args.get("q") or "").strip()
    # tags: 다중 선택(예: tags=dp&tags=bf)
    selected_tags = [t.strip() for t in request.args.getlist("tags") if t.strip()]

    def contains(hay, needle):
        try:
            return needle.lower() in str(hay or "").lower()
        except Exception:
            return False

    if q:
        # 선택된 필드가 있으면 그 필드들에서 OR 매칭, 없으면 전체 필드에서 OR 매칭
        def match_keywords(it):
            checks = []
            if not fields:
                checks = [
                    contains(it.get("title"), q),
                    contains(it.get("body"), q),
                    contains(it.get("problem_ref"), q),
                    any(contains(t, q) for t in (it.get("tags") or [])),
                ]
            else:
                if "title" in fields:
                    checks.append(contains(it.get("title"), q))
                if "body" in fields:
                    checks.append(contains(it.get("body"), q))
                if "ref" in fields:
                    checks.append(contains(it.get("problem_ref"), q))
                if "tag" in fields:
                    checks.append(any(contains(t, q) for t in (it.get("tags") or [])))
            return any(checks)

        items = [it for it in items if match_keywords(it)]

    if selected_tags:
        # 선택한 태그 중 하나라도 포함(OR)
        def has_any_tag(it):
            tags = it.get("tags") or []
            return any(t in tags for t in selected_tags)
        items = [it for it in items if has_any_tag(it)]

    # 정렬
    sort = (request.args.get("sort") or "new").strip()
    if sort == "title":
        items.sort(key=lambda it: (it.get("title") or "").lower())
    elif sort == "old":
        items.sort(key=lambda it: it.get("created_ts") or 0)
    else:  # default: new
        items.sort(key=lambda it: it.get("created_ts") or 0, reverse=True)

    # 태그 옵션(템플릿 멀티선택용) - 전체 데이터 기준으로 제공
    all_tags = sorted({t for it in items_all for t in (it.get("tags") or [])})

    # 페이지네이션
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    try:
        size = int(request.args.get("size", 5))
    except ValueError:
        size = 5
    size = min(max(1, size), 50)
    total = len(items)
    pages = max(1, math.ceil(total / size))
    page = min(page, pages)
    start = (page - 1) * size
    end = start + size
    page_items = items[start:end]

    return render_template(
        "problems/problems_list.html",
        items=page_items,
        q=q,
        fields=fields,
        selected_tags=selected_tags,
        all_tags=all_tags,
    sort=sort,
        page=page,
        size=size,
        total=total,
        pages=pages,
    )


@app.route("/problems/new", methods=["GET", "POST"])
@login_required
def problem_new():
    if request.method == "POST":
        # TODO: DB 저장 후 상세로 이동
        return redirect(url_for("problems"))
    return render_template("problems/problem_new.html")



# ---------------- Auth pages ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        nxt = request.args.get("next") or request.form.get("next")

        user = users_col.find_one({"email": email})
        if not user or not check_password_hash(user.get("password_hash", ""), password):
            flash("이메일 또는 비밀번호가 올바르지 않습니다.", "error")
            return redirect(url_for("login", next=nxt) if nxt else url_for("login"))

        session["user_id"] = str(user.get("_id"))
        session["email"] = user.get("email")
        return redirect(nxt or url_for("index"))
    return render_template("login.html", next=request.args.get("next"))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        interests = request.form.getlist("interests")  # 체크박스 다중선택
        boj_id = request.form.get("bojId")
        leetcode_id = request.form.get("leetcodeId")
        terms = request.form.get("terms") == "on"

        if not terms:
            flash("이용약관에 동의해야 가입할 수 있습니다.", "error")
            return redirect(url_for("signup"))
        if not email or not password:
            flash("이메일과 비밀번호를 입력해주세요.", "error")
            return redirect(url_for("signup"))
        if password != confirm:
            flash("비밀번호가 일치하지 않습니다.", "error")
            return redirect(url_for("signup"))
        if users_col.find_one({"email": email}):
            flash("이미 가입된 이메일입니다.", "error")
            return redirect(url_for("signup"))

        users_col.insert_one({
            "email": email,
            "password_hash": generate_password_hash(password),
            "interests": interests,
            "bojId": boj_id,
            "leetcodeId": leetcode_id,
        })
        user = users_col.find_one({"email": email})
        session["user_id"] = str(user.get("_id"))
        session["email"] = user.get("email")
        return redirect(url_for("index"))
    # 선택 가능한 관심사 목록 (템플릿에 표시용)
    algorithm_categories = [
        "동적계획법(DP)", "그래프", "트리", "정렬", "탐색",
        "문자열", "수학", "그리디", "분할정복", "백트래킹",
    ]
    return render_template("signup.html", algorithm_categories=algorithm_categories)

# 간단한 비동기 작업
@celery.task
def add(x, y):
    return x + y


@app.route("/task")
def run_task():
    task = add.delay(2, 3)  # 비동기 실행
    return jsonify({"task_id": task.id})




@app.route("/result/<task_id>")
def get_result(task_id):
    task = add.AsyncResult(task_id)
    if task.state == "PENDING":
        return jsonify({"state": task.state})
    elif task.state == "SUCCESS":
        return jsonify({"state": task.state, "result": task.result})
    else:
        # 실패/예외 처리
        return jsonify({"state": task.state, "info": str(task.info)})


if __name__ == "__main__":
    app.run(
        host=os.environ.get("FLASK_HOST", "0.0.0.0"),
        port=int(os.environ.get("FLASK_PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true",
        use_reloader=False,
    )

# ---------------- Problem Fetch API ----------------
# 간단한 메모리 캐시 (운영에선 Redis 권장)
CACHE = {}  # key: (platform,id) -> {"data":..., "ts":...}
TTL_SECONDS = 6 * 60 * 60  # 6시간

def cache_get(platform, pid):
    key = (platform, str(pid))
    v = CACHE.get(key)
    if v and time.time() - v["ts"] < TTL_SECONDS:
        return v["data"]
    return None

def cache_set(platform, pid, data):
    CACHE[(platform, str(pid))] = {"data": data, "ts": time.time()}

PATTERNS = [
    (r"^\s*BOJ\s*[-_ ]?\s*(\d+)\s*$", "boj"),
    (r"^\s*B[a]?ekjoon\s*[-_ ]?\s*(\d+)\s*$", "boj"),
    (r"^\s*Leet\s*Code\s*[-_ ]?\s*(\d+)\s*$", "leetcode"),
    (r"^\s*LeetCode\s*[-_ ]?\s*(\d+)\s*$", "leetcode"),
    (r"^\s*LC\s*[-_ ]?\s*(\d+)\s*$", "leetcode"),
]

def parse_ref(ref: str):
    ref = (ref or "").strip()
    for pat, platform in PATTERNS:
        m = re.match(pat, ref, re.IGNORECASE)
        if m:
            return platform, m.group(1)
    # 추가로 "12345"만 들어오면 BOJ로 간주 (옵션)
    if re.match(r"^\d+$", ref):
        return "boj", ref
    raise ValueError("지원하지 않는 형식입니다. 예) 'BOJ 12865', 'LeetCode 200'")

def fetch_boj(problem_id: str):
    url = f"https://www.acmicpc.net/problem/{problem_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.acmicpc.net/",
        "Connection": "keep-alive",
    }
    resp = requests.get(url, timeout=10, headers=headers)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    title_el = soup.select_one("#problem_title")
    desc = soup.select_one("#problem_description")
    inp = soup.select_one("#problem_input")
    out = soup.select_one("#problem_output")

    parts = []
    if desc: parts.append(str(desc))
    if inp: parts.append("<h3>입력</h3>" + str(inp))
    if out: parts.append("<h3>출력</h3>" + str(out))
    statement_html = "".join(parts) or "<p>(본문 파싱 실패)</p>"
    # 텍스트 버전
    text_parts = []
    if desc: text_parts.append(desc.get_text("\n", strip=True))
    if inp: text_parts.append("[입력]\n" + inp.get_text("\n", strip=True))
    if out: text_parts.append("[출력]\n" + out.get_text("\n", strip=True))
    statement_text = "\n\n".join(text_parts) if text_parts else "(본문 파싱 실패)"

    return {
        "platform": "boj",
        "id": str(problem_id),
        "title": title_el.get_text(strip=True) if title_el else f"BOJ {problem_id}",
        "difficulty": None,
        "tags": [],
        "url": url,
    "statement_html": statement_html,
    "statement_text": statement_text,
    "desc_html": (str(desc) if desc else None),
    "desc_text": (desc.get_text("\n", strip=True) if desc else None),
    }

def fetch_boj_meta_title(problem_id: str) -> str | None:
    try:
        r = requests.get(
            f"https://solved.ac/api/v3/problem/show?problemId={problem_id}",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code == 200:
            j = r.json()
            # titleKo가 존재하는 것으로 알려져 있음
            return j.get("titleKo") or j.get("title")
    except Exception:
        pass
    return None

def slugify_title_to_slug(title: str) -> str:
    # 간단 slugify: 소문자, 공백/특수문자 → '-'
    s = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip().lower())
    s = re.sub(r"-+", "-", s).strip('-')
    return s

def _lc_get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,ko-KR;q=0.8,ko;q=0.7",
        "Connection": "keep-alive",
    })
    # Optional: use env-provided cookies to avoid bot checks / paid-only limits
    lc_sess = os.environ.get("LEETCODE_SESSION")
    lc_csrf = os.environ.get("LEETCODE_CSRF") or os.environ.get("LEETCODE_CSRFTOKEN")
    if lc_sess:
        s.cookies.set("LEETCODE_SESSION", lc_sess, domain=".leetcode.com", path="/")
    if lc_csrf:
        s.cookies.set("csrftoken", lc_csrf, domain=".leetcode.com", path="/")
    try:
        s.get("https://leetcode.com/problemset/", timeout=8)
    except Exception:
        pass
    return s

def _lc_graphql(session: requests.Session, query: str, variables: dict, operation_name: str, referer: str | None = None, base_url: str = "https://leetcode.com"):
    url = f"{base_url.rstrip('/')}/graphql"
    headers = {
        "Content-Type": "application/json",
        "Referer": referer or f"{base_url.rstrip('/')}/problemset/",
        "Origin": base_url.rstrip('/'),
        "Accept": "application/json",
        "User-Agent": session.headers.get("User-Agent", "Mozilla/5.0"),
    }
    csrftoken = os.environ.get("LEETCODE_CSRF") or os.environ.get("LEETCODE_CSRFTOKEN") or session.cookies.get("csrftoken")
    if csrftoken:
        headers["x-csrftoken"] = csrftoken
    payload = {"query": query, "variables": variables, "operationName": operation_name}
    resp = session.post(url, json=payload, headers=headers, timeout=12)
    resp.raise_for_status()
    j = resp.json()
    if isinstance(j, dict) and j.get("errors"):
        raise RuntimeError(f"LeetCode GraphQL error: {j['errors'][0].get('message')}")
    return j.get("data")

def _lc_search_first(session: requests.Session, keyword: str):
    query = (
        "query problemsetQuestionList($categorySlug: String, $skip: Int, $limit: Int, $filters: QuestionListFilterInput) {"
        "  problemsetQuestionList(categorySlug: $categorySlug, skip: $skip, limit: $limit, filters: $filters) {"
        "    total questions { title titleSlug difficulty isPaidOnly topicTags { name slug } frontendQuestionId }"
        "  }"
        "}"
    )
    variables = {"categorySlug": "", "skip": 0, "limit": 5, "filters": {"searchKeywords": keyword}}
    data = _lc_graphql(session, query, variables, "problemsetQuestionList")
    lst = (data or {}).get("problemsetQuestionList") or {}
    questions = lst.get("questions") or []
    if not questions:
        return None
    lower = keyword.strip().lower()
    for q in questions:
        if str(q.get("title", "")).strip().lower() == lower:
            return q
    return questions[0]

def _translate_html_to_ko(html: str) -> str | None:
    api_key = os.environ.get("GOOGLE_TRANSLATE_API_KEY")
    if not api_key:
        return None
    try:
        url = "https://translation.googleapis.com/language/translate/v2"
        payload = {
            "q": html,
            "target": "ko",
            "format": "html",
        }
        resp = requests.post(f"{url}?key={api_key}", json=payload, timeout=12)
        resp.raise_for_status()
        j = resp.json()
        return ((j or {}).get("data") or {}).get("translations", [{}])[0].get("translatedText")
    except Exception:
        return None

def _translate_text_to_ko(text: str) -> str | None:
    api_key = os.environ.get("GOOGLE_TRANSLATE_API_KEY")
    if not api_key or not text:
        return None

@app.get("/api/_debug/test-translate")
def api_test_translate():
    sample = request.args.get("text", "Hello world")
    ok = True
    info = {}
    try:
        html = _translate_html_to_ko(f"<p>{sample}</p>")
        txt = _translate_text_to_ko(sample)
        return jsonify({
            "ok": True,
            "env": {
                "AUTO": os.environ.get("LEETCODE_AUTO_TRANSLATE_KO"),
                "HAS_KEY": bool(os.environ.get("GOOGLE_TRANSLATE_API_KEY")),
            },
            "result": {"html": html, "text": txt},
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "env": {
                "AUTO": os.environ.get("LEETCODE_AUTO_TRANSLATE_KO"),
                "HAS_KEY": bool(os.environ.get("GOOGLE_TRANSLATE_API_KEY")),
            },
        }), 500
    try:
        url = "https://translation.googleapis.com/language/translate/v2"
        payload = {"q": text, "target": "ko", "format": "text"}
        resp = requests.post(f"{url}?key={api_key}", json=payload, timeout=12)
        resp.raise_for_status()
        j = resp.json()
        return ((j or {}).get("data") or {}).get("translations", [{}])[0].get("translatedText")
    except Exception:
        return None

def fetch_leetcode_by_slug(slug: str, session: requests.Session | None = None):
    session = session or _lc_get_session()
    query = (
        "query questionData($titleSlug: String!) {"
        "  question(titleSlug: $titleSlug) { title translatedTitle titleSlug content translatedContent difficulty topicTags { name slug } }"
        "}"
    )
    variables = {"titleSlug": slug}
    try:
        data = _lc_graphql(session, query, variables, "questionData", referer=f"https://leetcode.com/problems/{slug}/description/", base_url="https://leetcode.com")
    except Exception as primary_err:
        # optional fallback to leetcode.cn
        if (os.environ.get("LEETCODE_CN_FALLBACK", "false").lower() == "true"):
            data = _lc_graphql(session, query, variables, "questionData", referer=f"https://leetcode.cn/problems/{slug}/description/", base_url="https://leetcode.cn")
        else:
            raise primary_err
    q = (data or {}).get("question") or {}
    # Prefer translated fields if available, but if AUTO_TRANSLATE=true, force translate to Korean
    title = q.get("translatedTitle") or q.get("title") or slug.replace('-', ' ').title()
    content_html = q.get("translatedContent") or q.get("content")
    if os.environ.get("LEETCODE_AUTO_TRANSLATE_KO", "false").lower() == "true":
        t_html = _translate_html_to_ko(content_html or "")
        if t_html:
            content_html = t_html
        t_title = _translate_text_to_ko(title)
        if t_title:
            title = t_title
    content_text = None
    if content_html:
        try:
            content_text = BeautifulSoup(content_html, "html.parser").get_text("\n", strip=True)
        except Exception:
            content_text = None
    difficulty = q.get("difficulty")
    tags = [t.get("name") for t in (q.get("topicTags") or []) if t and t.get("name")]
    return {
        "platform": "leetcode",
    "id": slug,
        "title": title,
        "difficulty": difficulty,
        "tags": tags,
        "url": f"https://leetcode.com/problems/{slug}/",
        "statement_html": content_html,
        "statement_text": content_text,
    }

@app.get("/api/fetch-problem")
def api_fetch_problem():
    # 우선 platform+q 모드 우선 처리
    platform_param = (request.args.get("platform") or "").strip().lower()
    q = (request.args.get("q") or "").strip()
    if platform_param in {"boj", "baekjoon", "leetcode"}:
        platform = "boj" if platform_param in {"boj", "baekjoon"} else "leetcode"
        try:
            if platform == "boj":
                if not re.match(r"^\d+$", q):
                    return jsonify({"ok": False, "error": "백준은 숫자만 입력하세요 (예: 2839)"}), 400
                pid = q
                cached = cache_get(platform, pid)
                if cached:
                    return jsonify({"ok": True, "data": cached})
                try:
                    data = fetch_boj(pid)
                    cache_set(platform, pid, data)
                    return jsonify({"ok": True, "data": data})
                except Exception as e:
                    # 부분 성공: solved.ac 메타만으로 제목/링크 채워서 반환
                    title = fetch_boj_meta_title(pid) or f"BOJ {pid}"
                    partial = {
                        "platform": "boj",
                        "id": str(pid),
                        "title": title,
                        "difficulty": None,
                        "tags": [],
                        "url": f"https://www.acmicpc.net/problem/{pid}",
                        "statement_html": "<p>(본문 불러오기 실패)</p>",
                        "statement_text": "(본문 불러오기 실패)",
                        "desc_text": None,
                    }
                    return jsonify({"ok": True, "data": partial, "warning": f"본문 파싱 실패: {e}"}), 200
            else:  # leetcode
                if not q:
                    return jsonify({"ok": False, "error": "LeetCode는 제목을 입력하세요 (예: Two Sum)"}), 400
                # 우선 GraphQL 검색으로 slug 해석 시도
                slug = None
                try:
                    s = _lc_get_session()
                    hit = _lc_search_first(s, q)
                    if hit and hit.get("titleSlug"):
                        slug = hit.get("titleSlug")
                except Exception:
                    pass
                if not slug:
                    slug = slugify_title_to_slug(q)
                pid = slug
                cached = cache_get(platform, pid)
                if cached:
                    return jsonify({"ok": True, "data": cached})
                try:
                    # minimal retry once if first attempt fails
                    try:
                        data = fetch_leetcode_by_slug(slug, session=s)
                    except Exception:
                        time.sleep(0.6)
                        data = fetch_leetcode_by_slug(slug, session=s)
                    cache_set(platform, pid, data)
                    return jsonify({"ok": True, "data": data})
                except Exception as e:
                    partial = {
                        "platform": "leetcode",
                        "id": slug,
                        "title": q,
                        "difficulty": None,
                        "tags": [],
                        "url": f"https://leetcode.com/problems/{slug}/",
                        "statement_html": "<p>(본문 불러오기 실패)</p>",
                        "statement_text": "(본문 불러오기 실패)",
                        "hint": "환경 변수 LEETCODE_SESSION/LEETCODE_CSRF 설정 시 성공률이 올라갑니다.",
                    }
                    return jsonify({"ok": True, "data": partial, "warning": f"본문 파싱 실패: {e}"}), 200
        except Exception as e:
            fallback = {
                "platform": platform,
                "id": str(q),
                "title": (q or f"{platform.upper()}"),
                "difficulty": None,
                "tags": [],
                "url": None,
                "statement_html": "<p>(본문 불러오기 실패)</p>",
                "statement_text": "(본문 불러오기 실패)",
            }
            return jsonify({"ok": False, "data": fallback, "error": f"가져오기 실패: {e}"}), 502

    # 호환: ref 문자열 모드
    ref = request.args.get("ref", "")
    try:
        platform, pid = parse_ref(ref)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    cached = cache_get(platform, pid)
    if cached:
        return jsonify({"ok": True, "data": cached})

    try:
        if platform == "boj":
            data = fetch_boj(pid)
        elif platform == "leetcode":
            # ref에서 온 leetcode는 숫자라고 가정 → 간단 처리 불가 시 에러 안내
            if not re.match(r"^\d+$", str(pid)):
                return jsonify({"ok": False, "error": "LeetCode는 제목을 입력하세요 (platform=leetcode&q=Two Sum)"}), 400
            # 숫자 매핑은 제거했으므로 안내만 제공
            return jsonify({"ok": False, "error": "LeetCode는 제목으로 요청하세요: /api/fetch-problem?platform=leetcode&q=Two%20Sum"}), 400
        else:
            return jsonify({"ok": False, "error": "지원하지 않는 플랫폼"}), 400
        cache_set(platform, pid, data)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        fallback = {
            "platform": platform,
            "id": str(pid),
            "title": f"{platform.upper()} {pid}",
            "difficulty": None,
            "tags": [],
            "url": None,
            "statement_html": "<p>(본문 불러오기 실패)</p>",
            "statement_text": "(본문 불러오기 실패)",
        }
        return jsonify({"ok": False, "data": fallback, "error": f"가져오기 실패: {e}"}), 502
