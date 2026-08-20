FROM python:3.11-slim

WORKDIR /app

# Copia los requerimientos e instálalos
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todos los archivos de tu proyecto
COPY . .

# Ejecuta el bot (asegúrate de que el archivo en GitHub se llame discord_bot.py)
CMD ["python", "discord_bot.py"]
