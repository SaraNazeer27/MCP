from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, List
import uvicorn

app = FastAPI(title="Books API", description="A simple books management API")


class Book(BaseModel):
    title: str
    author: str
    isbn: str = None
    year: int = None


class BookResponse(BaseModel):
    id: int
    title: str
    author: str
    isbn: str = None
    year: int = None


# In-memory storage
books: Dict[int, Book] = {}
next_id = 1


def _init_sample_books():
    global next_id
    samples = [
        {"title": "The Great Gatsby", "author": "F. Scott Fitzgerald", "isbn": "978-0-7432-7356-5", "year": 1925},
        {"title": "To Kill a Mockingbird", "author": "Harper Lee", "isbn": "978-0-06-112008-4", "year": 1960},
        {"title": "1984", "author": "George Orwell", "isbn": "978-0-452-28423-4", "year": 1949},
        {"title": "Pride and Prejudice", "author": "Jane Austen", "isbn": "978-0-14-143951-8", "year": 1813},
        {"title": "Moby-Dick", "author": "Herman Melville", "isbn": "978-0-14-243724-7", "year": 1851},
    ]
    for book_data in samples:
        books[next_id] = Book(**book_data)
        next_id += 1


_init_sample_books()


@app.get("/")
def root():
    return {"message": "Books API is running!", "total_books": len(books)}


@app.get("/books", response_model=List[BookResponse])
def get_all_books():
    """Get all books from the library"""
    result = []
    for book_id, book in books.items():
        result.append(BookResponse(id=book_id, **book.dict()))
    return result


@app.get("/books/{book_id}", response_model=BookResponse)
def get_book(book_id: int):
    """Get a specific book by ID"""
    if book_id not in books:
        raise HTTPException(status_code=404, detail=f"Book with ID {book_id} not found")
    book = books[book_id]
    return BookResponse(id=book_id, **book.dict())


@app.post("/books", response_model=BookResponse)
def create_book(book: Book):
    """Create a new book"""
    global next_id
    book_id = next_id
    books[book_id] = book
    next_id += 1
    return BookResponse(id=book_id, **book.dict())


@app.put("/books/{book_id}", response_model=BookResponse)
def update_book(book_id: int, book: Book):
    """Update an existing book"""
    if book_id not in books:
        raise HTTPException(status_code=404, detail=f"Book with ID {book_id} not found")
    books[book_id] = book
    return BookResponse(id=book_id, **book.dict())


@app.delete("/books/{book_id}")
def delete_book(book_id: int):
    """Delete a book by ID"""
    if book_id not in books:
        raise HTTPException(status_code=404, detail=f"Book with ID {book_id} not found")

    deleted_book = books[book_id]
    del books[book_id]
    return {
        "message": f"Book '{deleted_book.title}' by {deleted_book.author} has been deleted",
        "deleted_book": BookResponse(id=book_id, **deleted_book.dict())
    }


@app.get("/books/search/{query}")
def search_books(query: str):
    """Search books by title or author"""
    results = []
    query_lower = query.lower()

    for book_id, book in books.items():
        if (query_lower in book.title.lower() or
                query_lower in book.author.lower()):
            results.append(BookResponse(id=book_id, **book.dict()))

    if not results:
        raise HTTPException(status_code=404, detail=f"No books found matching '{query}'")

    return {
        "query": query,
        "found": len(results),
        "books": results
    }


@app.get("/stats")
def get_stats():
    """Get library statistics"""
    if not books:
        return {"total_books": 0, "authors": [], "oldest_book": None, "newest_book": None}

    authors = list(set(book.author for book in books.values()))
    years = [book.year for book in books.values() if book.year]

    return {
        "total_books": len(books),
        "total_authors": len(authors),
        "authors": sorted(authors),
        "oldest_book": min(years) if years else None,
        "newest_book": max(years) if years else None
    }


if __name__ == "__main__":
    print("Starting Books API server...")
    print("API Documentation available at: http://localhost:8000/docs")
    uvicorn.run("fast_api_server:app", host="127.0.0.1", port=8000, reload=True)
