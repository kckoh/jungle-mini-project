# app.py
from celery import Celery
from flask import Flask, jsonify, render_template, request, redirect, url_for, session
from functools import wraps
from pymongo import MongoClient
import os
from openai import OpenAI

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')  # 실제 운영에서는 반드시 환경 변수로 관리

# 로그인 체크 데코레이터
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

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

@app.get("/")
@login_required
def index():
    return render_template('hello.html')

# 예시: 보호된 페이지
# @app.route('/protected')
# @login_required
# def protected_page():
#     return render_template('protected.html')

@app.get("/db/ping")
def db_ping():
    try:
        db.command("ping")
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# 페이지 라우트
@app.get("/login")
def login_page():
    if 'email' in session:  # 이미 로그인된 사용자는 메인 페이지로
        return redirect(url_for('index'))
    return render_template('login.html')

@app.get("/signup")
def signup_page():
    if 'email' in session:  # 이미 로그인된 사용자는 메인 페이지로
        return redirect(url_for('index'))
    return render_template('signup.html')

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for('login_page'))

# API 라우트
@app.post("/api/login")
def login():
    try:
        data = request.form
        email = data.get('email')
        password = data.get('password')
        
        app.logger.info(f"Login attempt for email: {email}")
        
        user = db.users.find_one({"email": email, "password": password})
        if user:
            session['email'] = email
            app.logger.info(f"Login successful for email: {email}")
            return redirect(url_for('index'))
        else:
            app.logger.warning(f"Login failed for email: {email}")
            return redirect(url_for('login_page'))
            
    except Exception as e:
        app.logger.error(f"Login error for email {email if 'email' in locals() else 'unknown'}: {str(e)}")
        return redirect(url_for('login_page'))

@app.post("/api/signup")
def signup():
    try:
        data = request.form
        email = data.get('email')
        password = data.get('password')
        
        app.logger.info(f"Signup attempt for email: {email}")
        
        # 이메일 중복 체크
        if db.users.find_one({"email": email}):
            app.logger.warning(f"Signup failed - duplicate email: {email}")
            return redirect(url_for('signup_page'))
            
        # 데이터베이스에 저장
        db.users.insert_one({
            "email": email,
            "password": password  # 실제 운영에서는 반드시 암호화해야 합니다!
        })
        
        # 회원가입 후 바로 로그인 처리
        session['email'] = email
        app.logger.info(f"Signup and login successful for email: {email}")
        return redirect(url_for('index'))
            
    except Exception as e:
        app.logger.error(f"Signup error for email {email if 'email' in locals() else 'unknown'}: {str(e)}")
        return redirect(url_for('signup_page'))
    


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
    prompt =  """You are an expert algorithm problem analyst.
    Given a problem Title and Description, extract only the essential keywords required to solve it, and give a crisp Korean explanation for each keyword.

    OUTPUT RULES:
    - Return STRICTLY valid JSON with these 3 arrays:
      {
        "data_structures": [{"keyword": "...", "explanation": "..."}],
        "algorithms": [{"keyword": "...", "explanation": "..."}],
        "concepts": [{"keyword": "...", "explanation": "..."}]
      }
    - 3~8 items total; avoid duplicates and synonyms.
    - Explanations must be ≤ 2 sentences, Korean, practical (왜 필요한지/언제 쓰는지).

    Title: {{문제_타이틀}}
    Description: {{문제_설명}}
    """
    response = openai.chat.completions.create(
        model="gpt-4o-mini",  # or "gpt-4o-mini" if you want cheaper/faster
        messages=[
            {"role": "system", "content": "You are an expert algorithm problem analyst."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"}  # ensures valid JSON
    )

    # print(response.choices[0].message.content)
    parsed = json.loads(response.choices[0].message.content)
    doc.setdefault("data_structures",parsed['data_structures'])
    doc.setdefault("algorithms",parsed['algorithms'])
    doc.setdefault("concepts",parsed['concepts'])
    result = posts.insert_one(doc)

    _id = str(result.inserted_id)
    return _id

# @app.route("/task")
# def run_chatgpt_keywords():
#     task = get_store_keywords.delay()  # 비동기 실행
#     return jsonify({"task_id": task.id})


@app.route("/api/posts", methods=['POST'])
def create_post():
    data = request.get_json(silent=True)
    if data is None:
            return jsonify(error="JSON body required with Content-Type: application/json"), 400
    print("data===", data)

    # celery
    task = get_store_keywords.delay(data)

    return jsonify({"task_id": task.id }), 201



@app.route("/problems/<pid>")
def problem_detail(pid):
    # TODO: pid로 DB 조회
    item = {
        "id": pid,
        "title": "구간 합 구하기",
        "body": "정수 배열이 주어졌을 때, 구간 [l, r]의 합을 빠르게 구ƒ하는 문제입니다.",
        "created_at": "어제",
        # "code": "def prefix_sum(arr):\n    ...",
    }
    return render_template("problems/problem_detail.html", item=item)


if __name__ == "__main__":
    app.run(
        host=os.environ.get("FLASK_HOST", "0.0.0.0"),
        port=int(os.environ.get("FLASK_PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true",
        use_reloader=False,
    )
