# app.py
from celery import Celery

from flask import Flask, jsonify, render_template, request, redirect, url_for, session
from functools import wraps
from pymongo import MongoClient, ReturnDocument
import os
from openai import OpenAI
from datetime import datetime
import json
from bson import ObjectId


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# 로그인 체크 데코레이터
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# TODO
# problem_new.html -> frontend: javascript fetch API ; backend:
# problem_list.html -> backend API: GET /api/posts GET /api/search, frontend
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

def get_openai_key():
    key_file = os.environ.get("OPENAI_API_KEY_FILE")
    if key_file and os.path.exists(key_file):
        with open(key_file) as f:
            return f.read().strip()
    return os.environ.get("OPENAI_API_KEY")

openai = OpenAI(api_key=get_openai_key())

# MongoDB
client = MongoClient(os.environ.get("MONGO_URI"))
db = client.get_database()

# Post Table
posts = db["posts"]
users = db["users"]

# 메인화면
@app.get("/")
@login_required
def index():
    return render_template('base.html')

@app.get("/db/ping")
def db_ping():
    try:
        db.command("ping")
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# 로그인 화면으로 전환
@app.get("/login")
def login_page():
    if 'email' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

# 회원가입 화면으로 전환
@app.get("/signup")
def signup_page():
    if 'email' in session:
        return redirect(url_for('index'))
    return render_template('signup.html')

# 세션 삭제 및 로그아웃 처리
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login_page'))

# 로그인 처리(클라이언트에 json을 받아와 json으로 응답 보냄)
@app.post("/api/login")
def login():
    try:
        data = request.json
        email = data.get('email')
        password = data.get('password')
        
        user = users.find_one({"email": email, "password": password})
        if user:
            session['email'] = email
            return jsonify({"success": True, "message": "로그인에 성공했습니다."})
        else:
            return jsonify({"success": False, "message": "아이디와 비밀번호를 확인해주세요."})

    except Exception as e:
        return jsonify({"success": False, "message": "로그인 중 오류가 발생했습니다."})

# 회원가입 처리(클라이언트에 json을 받아와 DB에 데이터 입력 후 json으로 응답 보냄)
@app.post("/api/signup")
def signup():
    try:
        data = request.json
        email = data.get('email')
        password = data.get('password')

        
        if users.find_one({"email": email}):
            return jsonify({"success": False, "message": "이미 사용 중인 이메일입니다."})
            
        users.insert_one({
            "email": email,
            "password": password
        })

        return jsonify({"success": True, "message": "회원가입이 완료되었습니다."})

    except Exception as e:
        return jsonify({"success": False, "message": "회원가입 중 오류가 발생했습니다."})



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

@app.route("/test/chatgpt")
def get_chatgpt_test():
    response = openai.responses.create(
        model="gpt-4o",
        instructions="You are a coding assistant.",
        input="Hi",
    )

    return jsonify({"result":response.output_text})

def to_json(doc):
    """Mongo -> JSON-safe (ObjectId/datetime handling)."""
    if not doc:
        return None
    out = dict(doc)
    if "_id" in out:
        out["_id"] = str(out["_id"])
    for k, v in out.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
    return out
    # Optional: add server-side fields
def add_meta(d):
    d.setdefault("created_at", datetime.utcnow())
    d.setdefault("updated_at", datetime.utcnow())
    return d



@celery.task
def get_store_keywords(title_description):
    # save the data to the mongodb
    doc = add_meta(title_description)

    # call chatgpt API
    prompt =  f"""You are an expert algorithm problem analyst.
    Given a problem Title and Description, extract only the essential keywords required to solve it, and give a crisp Korean explanation for each keyword.
    OUTPUT RULES:
    - Return STRICTLY valid JSON with these 3 arrays:
    {{
      "data_structures": [{{"keyword": "...", "explanation": "..."}}],
      "algorithms": [{{"keyword": "...", "explanation": "..."}}],
      "concepts": [{{"keyword": "...", "explanation": "..."}}]
    }}

    - 3~8 items total; avoid duplicates and synonyms.
    - Explanations must be ≤ 2 sentences, Korean, practical (왜 필요한지/언제 쓰는지)

    Title: {title_description.get("title")}
    Description: {title_description.get("description")}
    """
    response = openai.chat.completions.create(
        model="gpt-4o-mini",  # or "gpt-4o-mini" if you want cheaper/faster
        messages=[
            {"role": "system", "content": "You are an expert algorithm problem analyst."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"}  # ensures valid JSON
    )

    parsed = json.loads(response.choices[0].message.content)
    doc.setdefault("data_structures",parsed['data_structures'])
    doc.setdefault("algorithms",parsed['algorithms'])
    doc.setdefault("concepts",parsed['concepts'])
    result = posts.insert_one(doc)

    _id = str(result.inserted_id)
    return _id



@celery.task
def get_store_aisuggestion(pid, post):

    # call chatgpt API
    prompt = f"""You are an expert algorithm problem analyst and senior code reviewer.

    Your tasks:
    1) From the given problem title/description, extract only essential keywords needed to solve it, each with a crisp Korean explanation (≤2 sentences).
    2) Analyze the provided code snippets for approach, complexity, correctness, edge cases, and maintainability.
    3) Propose what to study next (prioritized), with short justifications and concrete topic names.
    4) Analyze the given code snippets and criticize and comment what is good and bad and put those into code_suggestions and make a study plan based on it
    Rules:
    - Return STRICT, VALID JSON only (no extra text).
    - All field values (summary, explanation, why, etc.) **must be written in Korean**.
    - Use this schema:
    {{
      "code_review": {{
        "summary": "...",
        "approach": "...",
        "time_complexity": "예: O(N log N)",
        "space_complexity": "예: O(N)",
        "edge_cases_missing": ["..."],
        "test_cases_suggested": ["입력/출력 예시 ..."],
        "code_suggestions": ["..."],
      }},
      "study_plan": [
        {{"topic": "...", "why": "...", "what_to_focus": ["...", "..."] }}
      ]
    }}

    Title: {post.get("title", "")}
    Description: {post.get("description", "")}

    Code Snippets:
    {post.get("codeSnippets", "")}
    """
    print("promt===",prompt)
    response = openai.chat.completions.create(
        model="gpt-4o-mini",  # or "gpt-4o-mini" if you want cheaper/faster
        messages=[
            {"role": "system", "content": "You are an expert algorithm problem analyst and senior code reviewer."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"}  # ensures valid JSON
    )

    print("response====",response.choices[0].message.content)
    deserialized = json.loads(response.choices[0].message.content)
    aisuggestion = {'aiSuggestion': deserialized}
    # doc.setdefault("data_structures",parsed['data_structures'])
    # doc.setdefault("algorithms",parsed['algorithms'])
    # doc.setdefault("concepts",parsed['concepts'])
    # result = posts.insert_one(doc)
    result = posts.find_one_and_update(
        {"_id": ObjectId(pid)},
        {"$set": aisuggestion},
        return_document=ReturnDocument.AFTER
    )
    # _id = str(result.inserted_id)
    return "hello"



@app.route("/api/posts", methods=['POST'])
def create_post():
    data = request.get_json(silent=True)
    if data is None:
            return jsonify(error="JSON body required with Content-Type: application/json"), 400
    # celery
    task = get_store_keywords.delay(data)

    return jsonify({"task_id": task.id }), 201



# assuming that pid = objectid from the mongodb
@app.route("/problems/<pid>")
@login_required
def problem_detail(pid):
    try:
        result = posts.find_one({"_id": ObjectId(pid)})
    except Exception as e:
        return redirect("/")
    if not result:
        return redirect("/")
    # fake data
    item = {
        "id": pid,
        "title": result["title"],
        "description": result['description'],
        "created_at": result['created_at'].date(),
        # "code": "def prefix_sum(arr):\n    ...",
    }
    if 'codeSnippets' in result:
        item.setdefault('code',result['codeSnippets'] )
    if 'aiSuggestion' in result:
        item.setdefault('aiSuggestion', result['aiSuggestion'])
    # unpack data_structures, algorithms, concepts
    data = result['data_structures'] + result['algorithms'] + result['concepts']

    keyword_solution = {
        item["keyword"]: item["explanation"]
        for item in data
    }

    return render_template("problems/problem_detail.html", item=item, keyword_solution=keyword_solution)

# TODO
# handle aisuggestion in the frontend

@app.route("/api/posts/<pid>", methods=['PATCH'])
def update_post(pid):
    data = request.get_json()
    print("data=====", data)
    try:
        result = posts.find_one_and_update(
            {"_id": ObjectId(pid)},
            {"$set": data},
            return_document=ReturnDocument.AFTER
        )
    except Exception as e:
        return redirect("/")

    # get the post by id

    # after then use celery to update the aisuggestion
    result["_id"] = str(result["_id"])
    task = get_store_aisuggestion.delay(pid,result)
    return data, 200


if __name__ == "__main__":
    app.run(
        host=os.environ.get("FLASK_HOST", "0.0.0.0"),
        port=int(os.environ.get("FLASK_PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true",
        use_reloader=False,
    )
