import asyncio
# Optional MCP SDK import with graceful fallback (supports both package names)
_MCP_IMPORT_ERROR = None
Server = None  # type: ignore
Tool = None  # type: ignore
TextContent = None  # type: ignore
stdio_server = None  # type: ignore

# Try the canonical 'mcp' package name first
try:
    from mcp.server import Server  # type: ignore
    from mcp.types import Tool, TextContent  # type: ignore
    from mcp.server.stdio import stdio_server  # type: ignore
except Exception as _e1:
    # Fallback to the alternative package name used by some distributions
    try:
        from modelcontextprotocol.server import Server  # type: ignore
        from modelcontextprotocol.types import Tool, TextContent  # type: ignore
        from modelcontextprotocol.server.stdio import stdio_server  # type: ignore
    except Exception as _e2:
        _MCP_IMPORT_ERROR = (_e1, _e2)
from pydantic import BaseModel
from typing import Dict, List, Any
import json


# Book model
class Book(BaseModel):
    title: str
    author: str


# In-memory storage
books: Dict[int, Book] = {}
next_id = 1


def _init_sample_books():
    global next_id
    samples = [
        {"title": "The Great Gatsby", "author": "F. Scott Fitzgerald"},
        {"title": "To Kill a Mockingbird", "author": "Harper Lee"},
        {"title": "1984", "author": "George Orwell"},
        {"title": "Pride and Prejudice", "author": "Jane Austen"},
        {"title": "Moby-Dick", "author": "Herman Melville"},
    ]
    for book in samples:
        books[next_id] = Book(**book)
        next_id += 1


_init_sample_books()

# Create MCP server (guarded if MCP SDK missing)
if Server is None:  # type: ignore
    raise SystemExit(
        "Missing dependency: MCP Python SDK is required to run this MCP server.\n"
        "Install one of the following package names (depends on distribution):\n"
        "  - pip install mcp\n"
        "  - pip install modelcontextprotocol\n"
        "Docs: https://github.com/modelcontextprotocol/python-sdk\n"
        f"Original import errors: {_MCP_IMPORT_ERROR}"
    )
server = Server("books-library")


@server.list_tools()
async def list_tools() -> List[Tool]:
    return [
        Tool(
            name="get_books",
            description="Get all books from the library",
            inputSchema={
                "type": "object",
                "properties": {},
            }
        ),
        Tool(
            name="get_book",
            description="Get a specific book by ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "book_id": {"type": "integer", "description": "The ID of the book"}
                },
                "required": ["book_id"]
            }
        ),
        Tool(
            name="create_book",
            description="Create a new book in the library",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The title of the book"},
                    "author": {"type": "string", "description": "The author of the book"}
                },
                "required": ["title", "author"]
            }
        ),
        Tool(
            name="update_book",
            description="Update an existing book in the library",
            inputSchema={
                "type": "object",
                "properties": {
                    "book_id": {"type": "integer", "description": "The ID of the book to update"},
                    "title": {"type": "string", "description": "The new title of the book"},
                    "author": {"type": "string", "description": "The new author of the book"}
                },
                "required": ["book_id", "title", "author"]
            }
        ),
        Tool(
            name="delete_book",
            description="Delete a book from the library by ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "book_id": {"type": "integer", "description": "The ID of the book to delete"}
                },
                "required": ["book_id"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    try:
        if name == "get_books":
            if not books:
                return [TextContent(type="text", text="No books found in the library.")]

            book_list = []
            for book_id, book in books.items():
                book_list.append(f"ID: {book_id}, Title: {book.title}, Author: {book.author}")

            return [TextContent(type="text", text=f"Books in library:\n" + "\n".join(book_list))]

        elif name == "get_book":
            book_id = arguments["book_id"]
            if book_id not in books:
                return [TextContent(type="text", text=f"Book with ID {book_id} not found.")]

            book = books[book_id]
            return [TextContent(type="text", text=f"Book ID {book_id}:\nTitle: {book.title}\nAuthor: {book.author}")]

        elif name == "create_book":
            global next_id
            book = Book(title=arguments["title"], author=arguments["author"])
            book_id = next_id
            books[book_id] = book
            next_id += 1
            return [TextContent(type="text",
                                text=f"Successfully created book with ID {book_id}:\nTitle: {book.title}\nAuthor: {book.author}")]

        elif name == "update_book":
            book_id = arguments["book_id"]
            if book_id not in books:
                return [TextContent(type="text", text=f"Book with ID {book_id} not found.")]

            book = Book(title=arguments["title"], author=arguments["author"])
            books[book_id] = book
            return [TextContent(type="text",
                                text=f"Successfully updated book ID {book_id}:\nTitle: {book.title}\nAuthor: {book.author}")]

        elif name == "delete_book":
            book_id = arguments["book_id"]
            if book_id not in books:
                return [TextContent(type="text", text=f"Book with ID {book_id} not found.")]

            deleted_book = books[book_id]
            del books[book_id]
            return [TextContent(type="text",
                                text=f"Successfully deleted book:\nTitle: {deleted_book.title}\nAuthor: {deleted_book.author}")]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error executing tool {name}: {str(e)}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())