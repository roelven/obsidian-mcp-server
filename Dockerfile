FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README.md ./
COPY obsidian_mcp_server/ ./obsidian_mcp_server/

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Create non-root user
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app
USER app

# Expose port for SSE transport
EXPOSE 8000

# Default command (can be overridden)
CMD ["obsidian-mcp-server", "--transport", "http", "--port", "8000"] 