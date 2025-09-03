# app.py
from celery import Celery
from flask import Flask, jsonify, render_template, request, redirect
from pymongo import MongoClient, ReturnDocument
import os
from openai import OpenAI
from datetime import datetime
import json
from bson import ObjectId

app = Flask(__name__)

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

# health check용 API
@app.get("/")
def index():
    return render_template('hello.html')

@app.get("/db/ping")
def db_ping():
    try:
        db.command("ping")
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

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
    prompt =  f"""You are an expert algorithm problem analyst.
    Given a problem Title and Description, extract only the essential keywords required to solve it, and give a crisp Korean explanation for each keyword.

    OUTPUT RULES:
    - Return STRICTLY valid JSON with these 3 arrays:
      {{
        "data_structures": [{"keyword": "...", "explanation": "..."}],
        "algorithms": [{"keyword": "...", "explanation": "..."}],
        "concepts": [{"keyword": "...", "explanation": "..."}]
      }}
    - 3~8 items total; avoid duplicates and synonyms.
    - Explanations must be ≤ 2 sentences, Korean, practical (왜 필요한지/언제 쓰는지).

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
    ...
    Rules:
    - Return STRICT, VALID JSON only (no extra text).
    - Use this schema:
    {{
      "code_review": {{
        "summary": "...",
        "approach": "...",
        "time_complexity": "e.g., O(N log N)",
        "space_complexity": "e.g., O(N)",
        "edge_cases_missing": ["..."],
        "test_cases_suggested": ["input/output example ..."],
        "refactoring_suggestions": ["..."]
      }},
      "study_plan": [
        {{"topic": "...", "why": "...", "what_to_focus": ["...", "..."] }}
      ],
      "uncertainties": [
        {{"item": "...", "reason": "...", "label": "모르겠습니다|추측입니다|확실하지 않음"}}
      ],
      "confidence": 0.0
    }}

    Title: {post.get("title", "")}
    Description: {post.get("description", "")}

    Code Snippets:
    {post.get("codeSnippets", "")}
    ...
    """
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
