PRESETS={
 'python-telegram':{'name':'Telegram Bot · Python','runtime':'python','entrypoint':'main.py','requirements':'pyTelegramBotAPI'},
 'python-aiogram':{'name':'Aiogram Bot · Python','runtime':'python','entrypoint':'bot.py','requirements':'aiogram'},
 'python-discord':{'name':'Discord Bot · Python','runtime':'python','entrypoint':'main.py','requirements':'discord.py'},
 'node-telegram':{'name':'Telegram Bot · Node.js','runtime':'node','entrypoint':'index.js','requirements':'node-telegram-bot-api'},
 'node-discord':{'name':'Discord Bot · Node.js','runtime':'node','entrypoint':'index.js','requirements':'discord.js'},
 'custom-python':{'name':'Custom Python Worker','runtime':'python','entrypoint':'main.py','requirements':'requirements.txt'},
 'custom-node':{'name':'Custom Node Worker','runtime':'node','entrypoint':'index.js','requirements':'package.json'},
}
