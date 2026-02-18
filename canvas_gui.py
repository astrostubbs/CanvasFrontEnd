#!/usr/bin/env python3
"""
Canvas Course Manager — Local web GUI for managing Canvas LMS courses.

Copyright (c) 2025 Christopher Stubbs, Harvard University
Licensed under CC BY-NC 4.0 — https://creativecommons.org/licenses/by-nc/4.0/
"""

import os
import json
import mimetypes
import subprocess
import threading
import requests
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from flask import Flask, render_template, jsonify, request, send_file, Response, stream_with_context

app = Flask(__name__)

# ── Configuration ──────────────────────────────────────────────────────
# All sensitive config comes from environment variables.
# Set these before running, or create a .env file (see .env.example).
CANVAS_API_URL = os.environ.get("CANVAS_API_URL", "https://canvas.instructure.com/api/v1")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN", "")
LOCAL_ROOT = os.path.expanduser(os.environ.get("LOCAL_ROOT", "~/Desktop"))

if not CANVAS_API_TOKEN:
    print("\n  WARNING: CANVAS_API_TOKEN environment variable is not set.")
    print("  Set it before running:  export CANVAS_API_TOKEN='your_token_here'")
    print("  See .env.example for all configuration options.\n")

HEADERS = {
    "Authorization": f"Bearer {CANVAS_API_TOKEN}",
    "Accept": "application/json",
}


def canvas_get(endpoint, params=None):
    """GET request to Canvas API with pagination support."""
    url = f"{CANVAS_API_URL}/{endpoint}"
    all_results = []
    while url:
        resp = requests.get(url, headers=HEADERS, params=params)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            all_results.extend(data)
        else:
            return data
        # Follow pagination
        links = resp.headers.get("Link", "")
        url = None
        for part in links.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
        params = None  # params already in the next URL
    return all_results


# ── Page Routes ────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/HarvardLogo.png")
def harvard_logo():
    return send_file(os.path.join(app.root_path, "HarvardLogo.png"), mimetype="image/png")


# ── Config API ─────────────────────────────────────────────────────────

@app.route("/api/config")
def api_config():
    """Return non-secret configuration for the frontend."""
    return jsonify({
        "canvas_url": CANVAS_API_URL.replace("/api/v1", ""),
    })


# ── Local File API ─────────────────────────────────────────────────────

@app.route("/api/local/tree")
def local_tree():
    """Return local directory tree as nested JSON."""
    root = request.args.get("path", LOCAL_ROOT)
    # Security: only allow paths under LOCAL_ROOT or home
    root = os.path.realpath(root)
    return jsonify(build_local_tree(root, depth=0, max_depth=3))


def build_local_tree(path, depth, max_depth):
    """Recursively build directory tree."""
    name = os.path.basename(path) or path
    node = {"name": name, "path": path, "type": "directory", "children": []}
    if depth >= max_depth:
        node["truncated"] = True
        return node
    try:
        entries = sorted(os.listdir(path))
        for entry in entries:
            if entry.startswith("."):
                continue
            full = os.path.join(path, entry)
            if os.path.isdir(full):
                node["children"].append(build_local_tree(full, depth + 1, max_depth))
            else:
                size = os.path.getsize(full)
                node["children"].append({
                    "name": entry,
                    "path": full,
                    "type": "file",
                    "size": size,
                    "size_display": format_size(size),
                })
    except PermissionError:
        pass
    return node


@app.route("/api/local/expand")
def local_expand():
    """Expand a single directory (lazy loading)."""
    path = request.args.get("path", "")
    path = os.path.realpath(path)
    return jsonify(build_local_tree(path, depth=0, max_depth=1))


@app.route("/api/local/download")
def local_download():
    """Serve a local file for download."""
    path = request.args.get("path", "")
    if os.path.isfile(path):
        return send_file(path, as_attachment=True)
    return jsonify({"error": "File not found"}), 404


# ── Canvas API ─────────────────────────────────────────────────────────

@app.route("/api/canvas/courses")
def canvas_courses():
    """List instructor's courses."""
    courses = canvas_get("courses", params={
        "enrollment_type": "teacher",
        "per_page": 50,
        "include[]": "term",
    })
    result = []
    for c in courses:
        result.append({
            "id": c["id"],
            "name": c.get("name", "Unnamed"),
            "code": c.get("course_code", ""),
            "term": c.get("term", {}).get("name", ""),
        })
    return jsonify(sorted(result, key=lambda x: x["name"]))


@app.route("/api/canvas/course/<int:course_id>/overview")
def canvas_overview(course_id):
    """Get course overview: modules (without items), assignments, pages.
    Module items are lazy-loaded via a separate endpoint to avoid blocking.
    The three API calls run in parallel to minimize wait time.
    """
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_modules = pool.submit(canvas_get, f"courses/{course_id}/modules", {"per_page": 50})
        f_assignments = pool.submit(canvas_get, f"courses/{course_id}/assignments", {"per_page": 50})
        f_pages = pool.submit(canvas_get, f"courses/{course_id}/pages", {"per_page": 50})
    modules = f_modules.result()
    assignments = f_assignments.result()
    pages = f_pages.result()

    return jsonify({
        "modules": [{
            "id": m["id"],
            "name": m.get("name", ""),
            "position": m.get("position"),
            "items_count": m.get("items_count", 0),
        } for m in modules],
        "assignments": [{
            "id": a["id"],
            "name": a.get("name", ""),
            "due_at": a.get("due_at"),
            "points_possible": a.get("points_possible"),
            "submission_types": a.get("submission_types", []),
            "html_url": a.get("html_url", ""),
        } for a in assignments],
        "pages": [{
            "url": p.get("url", ""),
            "title": p.get("title", ""),
            "updated_at": p.get("updated_at"),
            "html_url": p.get("html_url", ""),
        } for p in pages],
    })


@app.route("/api/canvas/course/<int:course_id>/module/<int:module_id>/items")
def canvas_module_items(course_id, module_id):
    """Lazy-load items for a single module."""
    items = canvas_get(
        f"courses/{course_id}/modules/{module_id}/items",
        params={"per_page": 50}
    )
    return jsonify(items)


@app.route("/api/canvas/course/<int:course_id>/folders")
def canvas_folders(course_id):
    """List all folders in the course file storage."""
    folders = canvas_get(f"courses/{course_id}/folders", params={"per_page": 50})
    return jsonify([{
        "id": f["id"],
        "name": f.get("name", ""),
        "full_name": f.get("full_name", ""),
        "parent_folder_id": f.get("parent_folder_id"),
        "files_count": f.get("files_count", 0),
        "folders_count": f.get("folders_count", 0),
    } for f in folders])


@app.route("/api/canvas/course/<int:course_id>/folder/<int:folder_id>/files")
def canvas_folder_files(course_id, folder_id):
    """List files in a specific folder."""
    files = canvas_get(f"folders/{folder_id}/files", params={"per_page": 50})
    return jsonify([{
        "id": f["id"],
        "display_name": f.get("display_name", ""),
        "filename": f.get("filename", ""),
        "size": f.get("size", 0),
        "size_display": format_size(f.get("size", 0)),
        "content_type": f.get("content-type", ""),
        "url": f.get("url", ""),
        "updated_at": f.get("updated_at"),
    } for f in files])


@app.route("/api/canvas/course/<int:course_id>/syllabus")
def canvas_syllabus(course_id):
    """Get course syllabus."""
    course = canvas_get(f"courses/{course_id}", params={"include[]": "syllabus_body"})
    return jsonify({
        "syllabus_body": course.get("syllabus_body", "<p>No syllabus set.</p>"),
    })


@app.route("/api/canvas/course/<int:course_id>/upload", methods=["POST"])
def canvas_upload(course_id):
    """Upload a local file to Canvas course files.
    Canvas upload is a 3-step process:
    1. POST to notify Canvas of the upload
    2. POST the file to the upload URL
    3. Confirm the upload
    """
    local_path = request.json.get("local_path", "")
    folder_id = request.json.get("folder_id", None)

    if not os.path.isfile(local_path):
        return jsonify({"error": f"Local file not found: {local_path}"}), 400

    filename = os.path.basename(local_path)
    filesize = os.path.getsize(local_path)

    # Step 1: Tell Canvas about the file
    step1_data = {
        "name": filename,
        "size": filesize,
        "content_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
    }
    if folder_id:
        step1_data["parent_folder_id"] = folder_id

    resp1 = requests.post(
        f"{CANVAS_API_URL}/courses/{course_id}/files",
        headers=HEADERS,
        json=step1_data,
    )
    if resp1.status_code not in (200, 201):
        return jsonify({"error": "Step 1 failed", "details": resp1.text}), resp1.status_code

    upload_info = resp1.json()
    upload_url = upload_info.get("upload_url")
    upload_params = upload_info.get("upload_params", {})

    # Step 2: Upload the file
    with open(local_path, "rb") as f:
        files_payload = {"file": (filename, f)}
        resp2 = requests.post(upload_url, data=upload_params, files=files_payload)

    if resp2.status_code not in (200, 201, 301, 302, 303):
        return jsonify({"error": "Step 2 failed", "details": resp2.text}), resp2.status_code

    # Step 3: Confirm (Canvas may redirect; follow it)
    result = resp2.json() if resp2.headers.get("Content-Type", "").startswith("application/json") else {}

    return jsonify({
        "success": True,
        "file": {
            "id": result.get("id"),
            "display_name": result.get("display_name", filename),
            "size": filesize,
        }
    })


@app.route("/api/canvas/file/<int:file_id>/download")
def canvas_file_download(file_id):
    """Download a file from Canvas to a local directory."""
    dest_dir = request.args.get("dest", LOCAL_ROOT)
    dest_dir = os.path.realpath(dest_dir)

    # Get file metadata
    file_info = canvas_get(f"files/{file_id}")
    download_url = file_info.get("url")
    filename = file_info.get("display_name", f"file_{file_id}")

    if not download_url:
        return jsonify({"error": "No download URL available"}), 400

    # Download
    resp = requests.get(download_url, stream=True)
    resp.raise_for_status()

    dest_path = os.path.join(dest_dir, filename)
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    return jsonify({
        "success": True,
        "path": dest_path,
        "filename": filename,
        "size": os.path.getsize(dest_path),
    })


# ── Assignment API ─────────────────────────────────────────────────────

@app.route("/api/canvas/course/<int:course_id>/assignments")
def canvas_assignments(course_id):
    """List all assignments in a course with full details."""
    assignments = canvas_get(f"courses/{course_id}/assignments", params={"per_page": 50})
    return jsonify([{
        "id": a["id"],
        "name": a.get("name", ""),
        "description": a.get("description", ""),
        "due_at": a.get("due_at"),
        "lock_at": a.get("lock_at"),
        "unlock_at": a.get("unlock_at"),
        "points_possible": a.get("points_possible"),
        "submission_types": a.get("submission_types", []),
        "published": a.get("published", False),
        "html_url": a.get("html_url", ""),
        "grading_type": a.get("grading_type", "points"),
        "position": a.get("position"),
    } for a in assignments])


@app.route("/api/canvas/course/<int:course_id>/assignments", methods=["POST"])
def canvas_create_assignment(course_id):
    """Create a new assignment."""
    data = request.json
    assignment_data = {
        "assignment[name]": data.get("name", "Untitled Assignment"),
        "assignment[submission_types][]": data.get("submission_types", ["online_text_entry"]),
        "assignment[published]": False,
    }
    if data.get("description"):
        assignment_data["assignment[description]"] = data["description"]
    if data.get("due_at"):
        assignment_data["assignment[due_at]"] = data["due_at"]
    if data.get("points_possible") is not None:
        assignment_data["assignment[points_possible]"] = data["points_possible"]
    if data.get("grading_type"):
        assignment_data["assignment[grading_type]"] = data["grading_type"]

    resp = requests.post(
        f"{CANVAS_API_URL}/courses/{course_id}/assignments",
        headers=HEADERS,
        data=assignment_data,
    )
    resp.raise_for_status()
    a = resp.json()
    return jsonify({"success": True, "assignment": {"id": a["id"], "name": a.get("name")}})


@app.route("/api/canvas/course/<int:course_id>/assignments/<int:assignment_id>", methods=["PUT"])
def canvas_update_assignment(course_id, assignment_id):
    """Update an existing assignment."""
    data = request.json
    assignment_data = {}

    if "name" in data:
        assignment_data["assignment[name]"] = data["name"]
    if "description" in data:
        assignment_data["assignment[description]"] = data["description"]
    if "due_at" in data:
        assignment_data["assignment[due_at]"] = data["due_at"] or ""
    if "lock_at" in data:
        assignment_data["assignment[lock_at]"] = data["lock_at"] or ""
    if "unlock_at" in data:
        assignment_data["assignment[unlock_at]"] = data["unlock_at"] or ""
    if "points_possible" in data:
        assignment_data["assignment[points_possible]"] = data["points_possible"]
    if "published" in data:
        assignment_data["assignment[published]"] = data["published"]
    if "grading_type" in data:
        assignment_data["assignment[grading_type]"] = data["grading_type"]

    resp = requests.put(
        f"{CANVAS_API_URL}/courses/{course_id}/assignments/{assignment_id}",
        headers=HEADERS,
        data=assignment_data,
    )
    resp.raise_for_status()
    return jsonify({"success": True})


@app.route("/api/canvas/course/<int:course_id>/assignments/<int:assignment_id>", methods=["DELETE"])
def canvas_delete_assignment(course_id, assignment_id):
    """Delete an assignment."""
    resp = requests.delete(
        f"{CANVAS_API_URL}/courses/{course_id}/assignments/{assignment_id}",
        headers=HEADERS,
    )
    resp.raise_for_status()
    return jsonify({"success": True})


# ── Quiz API ───────────────────────────────────────────────────────────

@app.route("/api/canvas/course/<int:course_id>/quizzes")
def canvas_quizzes(course_id):
    """List all quizzes in a course."""
    quizzes = canvas_get(f"courses/{course_id}/quizzes", params={"per_page": 50})
    return jsonify([{
        "id": q["id"],
        "title": q.get("title", ""),
        "description": q.get("description", ""),
        "quiz_type": q.get("quiz_type", "assignment"),
        "points_possible": q.get("points_possible"),
        "question_count": q.get("question_count", 0),
        "time_limit": q.get("time_limit"),
        "published": q.get("published", False),
        "due_at": q.get("due_at"),
        "html_url": q.get("html_url", ""),
    } for q in quizzes])


@app.route("/api/canvas/course/<int:course_id>/quizzes", methods=["POST"])
def canvas_create_quiz(course_id):
    """Create a new quiz."""
    data = request.json
    quiz_data = {
        "quiz[title]": data.get("title", "Untitled Quiz"),
        "quiz[description]": data.get("description", ""),
        "quiz[quiz_type]": data.get("quiz_type", "assignment"),
        "quiz[time_limit]": data.get("time_limit"),
        "quiz[shuffle_answers]": data.get("shuffle_answers", True),
        "quiz[published]": False,  # Always create as draft
    }
    if data.get("points_possible"):
        quiz_data["quiz[points_possible]"] = data["points_possible"]
    if data.get("due_at"):
        quiz_data["quiz[due_at]"] = data["due_at"]

    resp = requests.post(
        f"{CANVAS_API_URL}/courses/{course_id}/quizzes",
        headers=HEADERS,
        data=quiz_data,
    )
    resp.raise_for_status()
    q = resp.json()
    return jsonify({"success": True, "quiz": {"id": q["id"], "title": q.get("title")}})


@app.route("/api/canvas/course/<int:course_id>/quizzes/<int:quiz_id>/questions")
def canvas_quiz_questions(course_id, quiz_id):
    """List questions in a quiz."""
    questions = canvas_get(
        f"courses/{course_id}/quizzes/{quiz_id}/questions",
        params={"per_page": 50}
    )
    return jsonify(questions)


@app.route("/api/canvas/course/<int:course_id>/quizzes/<int:quiz_id>/questions", methods=["POST"])
def canvas_add_question(course_id, quiz_id):
    """Add a question to a quiz."""
    data = request.json
    q_type = data.get("question_type", "multiple_choice_question")

    question_data = {
        "question[question_name]": data.get("question_name", "Question"),
        "question[question_text]": data.get("question_text", ""),
        "question[question_type]": q_type,
        "question[points_possible]": data.get("points_possible", 1),
        "question[position]": data.get("position"),
    }

    # Add answers for applicable question types
    if "answers" in data:
        for i, ans in enumerate(data["answers"]):
            question_data[f"question[answers][{i}][answer_text]"] = ans.get("text", "")
            question_data[f"question[answers][{i}][answer_weight]"] = ans.get("weight", 0)
            if ans.get("comment"):
                question_data[f"question[answers][{i}][answer_comment]"] = ans["comment"]

    resp = requests.post(
        f"{CANVAS_API_URL}/courses/{course_id}/quizzes/{quiz_id}/questions",
        headers=HEADERS,
        data=question_data,
    )
    resp.raise_for_status()
    return jsonify({"success": True, "question": resp.json()})


@app.route("/api/canvas/course/<int:course_id>/quizzes/<int:quiz_id>/questions/<int:question_id>", methods=["PUT"])
def canvas_update_question(course_id, quiz_id, question_id):
    """Update an existing question."""
    data = request.json
    question_data = {}

    if "question_name" in data:
        question_data["question[question_name]"] = data["question_name"]
    if "question_text" in data:
        question_data["question[question_text]"] = data["question_text"]
    if "points_possible" in data:
        question_data["question[points_possible]"] = data["points_possible"]

    if "answers" in data:
        for i, ans in enumerate(data["answers"]):
            if ans.get("id"):
                question_data[f"question[answers][{i}][id]"] = ans["id"]
            question_data[f"question[answers][{i}][answer_text]"] = ans.get("text", "")
            question_data[f"question[answers][{i}][answer_weight]"] = ans.get("weight", 0)

    resp = requests.put(
        f"{CANVAS_API_URL}/courses/{course_id}/quizzes/{quiz_id}/questions/{question_id}",
        headers=HEADERS,
        data=question_data,
    )
    resp.raise_for_status()
    return jsonify({"success": True})


@app.route("/api/canvas/course/<int:course_id>/quizzes/<int:quiz_id>/questions/<int:question_id>", methods=["DELETE"])
def canvas_delete_question(course_id, quiz_id, question_id):
    """Delete a question from a quiz."""
    resp = requests.delete(
        f"{CANVAS_API_URL}/courses/{course_id}/quizzes/{quiz_id}/questions/{question_id}",
        headers=HEADERS,
    )
    resp.raise_for_status()
    return jsonify({"success": True})


@app.route("/api/canvas/course/<int:course_id>/quizzes/<int:quiz_id>/publish", methods=["PUT"])
def canvas_publish_quiz(course_id, quiz_id):
    """Publish or unpublish a quiz."""
    publish = request.json.get("published", True)
    resp = requests.put(
        f"{CANVAS_API_URL}/courses/{course_id}/quizzes/{quiz_id}",
        headers=HEADERS,
        data={"quiz[published]": publish},
    )
    resp.raise_for_status()
    return jsonify({"success": True})


# ── Page Content API ───────────────────────────────────────────────────

@app.route("/api/canvas/course/<int:course_id>/pages")
def canvas_list_pages(course_id):
    """List all pages in a course."""
    pages = canvas_get(f"courses/{course_id}/pages", params={"per_page": 100})
    return jsonify([{
        "url": p.get("url", ""),
        "title": p.get("title", ""),
        "published": p.get("published", False),
        "updated_at": p.get("updated_at"),
    } for p in pages])


@app.route("/api/canvas/course/<int:course_id>/pages/<path:page_url>")
def canvas_get_page(course_id, page_url):
    """Get a single page's content."""
    page = canvas_get(f"courses/{course_id}/pages/{page_url}")
    return jsonify({
        "url": page.get("url", ""),
        "title": page.get("title", ""),
        "body": page.get("body", ""),
        "published": page.get("published", False),
    })


@app.route("/api/canvas/course/<int:course_id>/pages/<path:page_url>", methods=["PUT"])
def canvas_update_page(course_id, page_url):
    """Update a page's content."""
    data = request.json
    page_data = {}
    if "body" in data:
        page_data["wiki_page[body]"] = data["body"]
    if "title" in data:
        page_data["wiki_page[title]"] = data["title"]
    if "published" in data:
        page_data["wiki_page[published]"] = data["published"]
    resp = requests.put(
        f"{CANVAS_API_URL}/courses/{course_id}/pages/{page_url}",
        headers=HEADERS,
        data=page_data,
    )
    resp.raise_for_status()
    return jsonify({"success": True})


# ── Student Analytics API ──────────────────────────────────────────────

@app.route("/api/canvas/course/<int:course_id>/students")
def canvas_students(course_id):
    """List enrolled students."""
    users = canvas_get(f"courses/{course_id}/users", params={
        "enrollment_type[]": "student",
        "per_page": 100,
    })
    return jsonify([{
        "id": u["id"],
        "name": u.get("name", ""),
        "sortable_name": u.get("sortable_name", ""),
        "email": u.get("email", ""),
    } for u in users])


@app.route("/api/canvas/course/<int:course_id>/analytics/student_summary")
def canvas_student_summary(course_id):
    """Get per-student analytics: current score, page views, participations."""
    try:
        summaries = canvas_get(
            f"courses/{course_id}/analytics/student_summaries",
            params={"per_page": 100}
        )
        return jsonify(summaries)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/canvas/course/<int:course_id>/analytics/progress")
def canvas_student_progress(course_id):
    """Comprehensive student progress data: submissions + scores."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_students = pool.submit(
            canvas_get, f"courses/{course_id}/users",
            {"enrollment_type[]": "student", "per_page": 100}
        )
        f_assignments = pool.submit(
            canvas_get, f"courses/{course_id}/assignments",
            {"per_page": 100}
        )
        f_enrollments = pool.submit(
            canvas_get, f"courses/{course_id}/enrollments",
            {"type[]": "StudentEnrollment", "per_page": 100, "state[]": "active"}
        )
        f_summaries = pool.submit(
            canvas_get, f"courses/{course_id}/analytics/student_summaries",
            {"per_page": 100}
        )

    students = f_students.result()
    assignments = f_assignments.result()
    enrollments = f_enrollments.result()
    try:
        summaries = f_summaries.result()
    except Exception:
        summaries = []

    # Build student map with enrollment grades
    student_map = {}
    for s in students:
        student_map[s["id"]] = {
            "id": s["id"],
            "name": s.get("sortable_name", s.get("name", "")),
            "current_score": None,
            "final_score": None,
        }
    for e in enrollments:
        uid = e.get("user_id")
        if uid in student_map and e.get("grades"):
            student_map[uid]["current_score"] = e["grades"].get("current_score")
            student_map[uid]["final_score"] = e["grades"].get("final_score")

    # Get submissions for graded assignments
    graded_assignments = [a for a in assignments if a.get("points_possible") and a["points_possible"] > 0]
    assignment_info = [{
        "id": a["id"],
        "name": a.get("name", ""),
        "points_possible": a.get("points_possible", 0),
        "due_at": a.get("due_at"),
    } for a in graded_assignments]

    # Fetch submissions in parallel batches
    submissions_by_student = {s["id"]: [] for s in students}
    def fetch_subs(assignment_id):
        try:
            return canvas_get(
                f"courses/{course_id}/assignments/{assignment_id}/submissions",
                {"per_page": 100}
            )
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fetch_subs, a["id"]): a for a in graded_assignments}
        for future in futures:
            a = futures[future]
            try:
                subs = future.result()
                for sub in subs:
                    uid = sub.get("user_id")
                    if uid in submissions_by_student:
                        submissions_by_student[uid].append({
                            "assignment_id": a["id"],
                            "score": sub.get("score"),
                            "submitted_at": sub.get("submitted_at"),
                            "workflow_state": sub.get("workflow_state"),
                            "late": sub.get("late", False),
                            "missing": sub.get("missing", False),
                        })
            except Exception:
                pass

    # Build access statistics from student summaries
    access_stats = {}
    for s in summaries:
        uid = s.get("id")
        if uid:
            access_stats[str(uid)] = {
                "page_views": s.get("page_views", 0),
                "participations": s.get("participations", 0),
            }

    return jsonify({
        "students": list(student_map.values()),
        "assignments": assignment_info,
        "submissions": {str(k): v for k, v in submissions_by_student.items()},
        "access_stats": access_stats,
    })


@app.route("/api/canvas/course/<int:course_id>/calendar_data")
def canvas_calendar_data(course_id):
    """Get assignments and quizzes with date ranges for calendar view."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_assignments = pool.submit(
            canvas_get, f"courses/{course_id}/assignments", {"per_page": 100}
        )
        f_quizzes = pool.submit(
            canvas_get, f"courses/{course_id}/quizzes", {"per_page": 100}
        )
    assignments = f_assignments.result()
    quizzes = f_quizzes.result()

    events = []
    for a in assignments:
        if a.get("due_at") or a.get("unlock_at"):
            events.append({
                "type": "assignment",
                "id": a["id"],
                "name": a.get("name", ""),
                "due_at": a.get("due_at"),
                "unlock_at": a.get("unlock_at"),
                "lock_at": a.get("lock_at"),
                "points": a.get("points_possible"),
                "html_url": a.get("html_url", ""),
            })
    for q in quizzes:
        if q.get("due_at"):
            events.append({
                "type": "quiz",
                "id": q["id"],
                "name": q.get("title", ""),
                "due_at": q.get("due_at"),
                "unlock_at": q.get("unlock_at"),
                "lock_at": q.get("lock_at"),
                "points": q.get("points_possible"),
                "html_url": q.get("html_url", ""),
            })
    return jsonify(events)


# ── AI Assistant (Claude via claude CLI) ───────────────────────────────

@app.route("/api/ai/chat", methods=["POST"])
def ai_chat():
    """Send a prompt to Claude Code CLI and stream the response."""
    data = request.json
    prompt = data.get("prompt", "")
    course_context = data.get("course_context", "")

    # Build a context-aware prompt
    full_prompt = prompt
    if course_context:
        full_prompt = f"Context: You are helping manage a Canvas LMS course. {course_context}\n\nUser request: {prompt}"

    def generate():
        try:
            # Inherit the current environment which should already have
            # ANTHROPIC_API_KEY, ANTHROPIC_BEDROCK_BASE_URL, etc. set
            # via trainingsetup.sh. Only set Bedrock vars if not already present.
            env = os.environ.copy()
            env.setdefault("ANTHROPIC_BEDROCK_BASE_URL", "https://apis.huit.harvard.edu/ais-bedrock-llm/v2")
            env.setdefault("CLAUDE_CODE_SKIP_BEDROCK_AUTH", "1")
            env.setdefault("CLAUDE_CODE_USE_BEDROCK", "1")

            proc = subprocess.Popen(
                [
                    "claude", "-p", full_prompt,
                    "--allowedTools", "mcp__Canvas__*",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )

            for line in proc.stdout:
                yield f"data: {json.dumps({'text': line})}\n\n"

            proc.wait()
            if proc.returncode != 0:
                err = proc.stderr.read()
                yield f"data: {json.dumps({'error': err})}\n\n"

            yield "data: [DONE]\n\n"

        except FileNotFoundError:
            yield f"data: {json.dumps({'error': 'Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code'})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Helpers ────────────────────────────────────────────────────────────

def format_size(size_bytes):
    """Format bytes to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


if __name__ == "__main__":
    print(f"\n  Canvas Course Manager")
    print(f"  Local root: {LOCAL_ROOT}")
    print(f"  Canvas API: {CANVAS_API_URL}")
    print(f"\n  Open http://localhost:5050 in your browser\n")
    app.run(host="127.0.0.1", port=5050, debug=True)
