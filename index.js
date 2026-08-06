let dotenv;
try 
{
  dotenv = require('dotenv');
} 
catch (error)
{
  dotenv = null;
}

if (dotenv) 
    {
  dotenv.config();
} 
else 
    {
  const fs = require('fs');
  const path = require('path');
  const envFile = path.resolve('.env');

  if (fs.existsSync(envFile)) {
    const lines = fs.readFileSync(envFile, 'utf8').split(/\r?\n/);
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;

      const separatorIndex = trimmed.indexOf('=');
      if (separatorIndex === -1) continue;

      const key = trimmed.slice(0, separatorIndex).trim();
      const value = trimmed.slice(separatorIndex + 1).trim();
      process.env[key] = value;
    }
  }
}

const openrouterApiKey = process.env.OPENROUTER_API_KEY;

if (!openrouterApiKey || openrouterApiKey.includes('your_'))
    {
  throw new Error('OpenRouter API key not found in .env file');
}

async function callOpenRouterAPI(messages)
{
  const url = 'https://openrouter.ai/api/v1/chat/completions';

  try
  {
    const response = await fetch(url,
        {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${openrouterApiKey}`,
        'Content-Type': 'application/json',
        'HTTP-Referer': 'http://localhost:3000',
        'X-Title': 'OpenRouter API Practice'
      },
      body: JSON.stringify({
        model: 'meta-llama/llama-3.1-8b-instruct',
        messages: messages,
        temperature: 0.7
      })
    });

    if (!response.ok) {
      const data = await response.json();
      throw new Error(`HTTP ${response.status}: ${JSON.stringify(data)}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Error calling OpenRouter API:', error);
    return null;
  }
}

const readline = require('readline');

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout
});

const messages = [];

function promptUser(query) {
  return new Promise((resolve) => rl.question(query, resolve));
}

async function chat() {
  console.log('Chatbot ready. Type "exit" or "quit" to end.\n');

  while (true) {
    const userInput = await promptUser('You: ');
    const trimmed = userInput.trim();

    if (!trimmed) continue;
    if (['exit', 'quit'].includes(trimmed.toLowerCase())) {
      console.log('Goodbye!');
      rl.close();
      return;
    }

    messages.push({ role: 'user', content: trimmed });

    try {
      const response = await callOpenRouterAPI(messages);
      if (response && response.choices?.[0]?.message?.content) {
        const reply = response.choices[0].message.content.trim();
        messages.push({ role: 'assistant', content: reply });
        console.log(`Bot: ${reply}\n`);
      } else {
        console.error('No valid response from OpenRouter. Try again.\n');
        messages.pop();
      }
    } catch (error) {
      console.error(`Error calling OpenRouter API: ${error}\n`);
      messages.pop();
    }
  }
}

chat().catch((error) => {
  console.error('Error in chat loop:', error);
  rl.close();
  process.exit(1);
});
