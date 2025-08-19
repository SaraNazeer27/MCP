from flask import Flask, request, jsonify, abort

app = Flask(__name__)

books = {
    1: {'id': 1, 'title': 'The Great Gatsby', 'author': 'F. Scott Fitzgerald'},
    2: {'id': 2, 'title': 'To Kill a Mockingbird', 'author': 'Harper Lee'},
    3: {'id': 3, 'title': '1984', 'author': 'George Orwell'}
}
next_id = 4

@app.route('/books', methods=['GET'])
def get_books():
    return jsonify(list(books.values()))

@app.route('/books/<int:book_id>', methods=['GET'])
def get_book(book_id):
    book = books.get(book_id)
    if not book:
        abort(404)
    return jsonify(book)

@app.route('/books', methods=['POST'])
def create_book():
    global next_id
    data = request.get_json()
    if not data or 'title' not in data or 'author' not in data:
        abort(400)
    book = {
        'id': next_id,
        'title': data['title'],
        'author': data['author']
    }
    books[next_id] = book
    next_id += 1
    return jsonify(book), 201

@app.route('/books/<int:book_id>', methods=['PUT'])
def update_book(book_id):
    data = request.get_json()
    if not data or 'title' not in data or 'author' not in data:
        abort(400)
    book = books.get(book_id)
    if not book:
        abort(404)
    book['title'] = data['title']
    book['author'] = data['author']
    return jsonify(book)

@app.route('/books/<int:book_id>', methods=['DELETE'])
def delete_book(book_id):
    if book_id not in books:
        abort(404)
    del books[book_id]
    return '', 204

if __name__ == '__main__':
    app.run(debug=True)

