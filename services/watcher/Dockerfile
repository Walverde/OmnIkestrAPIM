# Usa a mesma imagem base leve
FROM python:3.11-slim

# Define o diretório de trabalho
WORKDIR /app

# Variáveis de ambiente padrão do Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copia o arquivo de dependências
COPY requirements.txt .

# Instala as dependências do Python
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código do watcher
COPY . .

# Comando para rodar o script principal do watcher
CMD ["python", "main.py"]