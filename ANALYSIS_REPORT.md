# BLAZE NXT Bot - Code Examination Report

**File:** main.txt (4951 lines, ~217KB)
**Type:** Telegram Hosting Bot (pyTelegramBotAPI + Flask Keep Alive)
**Language:** Hinglish comments

---

### 1. Bot Kya Karta Hai?

Ye ek "Hosting as a Service" wala Telegram bot hai:
- Users `.py`, `.js` ya `.zip` files upload karte hain
- Bot unko server pe `upload_bots/<user_id>/` me save karta hai
- Auto-dependency install karta hai (`requirements.txt` / `package.json` se)
- `python script.py` ya `node script.js` se run karta hai
- Start / Stop / Restart / Logs / Delete ka control deta hai
- Owner -> Admin -> Premium -> Free User ka hierarchy hai

**Main Features:**
- Mandatory Channel Join Force (Force Subscribe)
- File Limit System (Free=2, Premium=20, Admin=999, Owner=Unlimited)
- Ban/Unban, Custom Limit, Subscription Management
- Broadcast to all users
- Manual `pip` / `npm` install system
- SQLite Database (`inf/bot_data.db`)

### 2. Architecture Overview
```
.env (BOT_TOKEN, OWNER_ID) -> Flask Keep Alive (port 8080)
      -> Telebot Polling
      -> /upload_bots/<user_id>/  (user files)
      -> inf/bot_data.db (sqlite)
```

---

### 3. CRITICAL SECURITY FLAWS - Sabse Badi Problem

#### A) Security Regex Pura Fail Hai
`check_code_security()` aur `scan_zip_security()` me ~300 regex patterns hain jo **almost har valid code ko block kar denge.**

Examples jo galat hain:
- `r'\bos\b'` -> kisi bhi file me "os" word aaya to block. Comment me "close" likhoge to bhi block ho sakta hai? Nahi, \b ki wajah se alag word, par fir bhi `import os` to 100% block.
- `r'\bre\b'` -> `re` module pure Python ka core hai, par tumne block kar diya. Har regex wala code fail.
- `r'\bopen\s*\('` -> File open karna hi block. Har normal bot me `open()` use hota hai.
- `r'\brequests\b'` -> Har Telegram bot me requests use hota hai, par block kar diya.
- `r'\btelebot\b'`, `r'\baiogram\b'` -> Khud hosting bot ka code hi block ho jayega.
- `r'\.jpg\b`, `\.png\b`, `\.pdf\b` -> Image ka naam aaya to block. Matlab koi bhi bot jo image bhejta hai woh reject.
- `r'\bls\b`, `r'\bcd\b`, `r'\bps\b`, `r'\bvps\b'` -> `ls` to har output string me aa sakta hai.
- `r'.*\{.*,}'` , `r'\^.*\$'` , `r'\[.*\]'` , `r'\(.*\)'` , `r'\?.*'` -> YE PURE REGEX KO HI BLOCK KAR RAHA HAI. Ye pattern khud har file me match ho jayega.
- `r'\benv\b'`, `r'\bfile\b'`, `r'\bmem\b'` -> Itne common words block hain.

**Result:** Legit user ka 95% code "Dangerous" bolke approval me chala jayega. Ye blacklist approach kabhi kaam nahi karega.

Sahi approach: Whitelist + Docker sandbox, AST parsing se `__import__('os')` jaise dynamic calls detect karo.

#### B) Arbitrary Code Execution (RCE as a Feature)
Bot literally user ka code `subprocess.Popen([sys.executable, script_path])` se host machine pe hi chalata hai. No Docker, No isolation, No CPU/RAM limit.

User `BOT_TOKEN` ko `os.environ` se read karke dusre users ke files delete kar sakta hai, ya server ko hi down kar dega agar security check bypass ho gaya.

#### C) Callback Data Parsing Bug
```python
_, script_owner_id_str, file_name = call.data.split('_', 2)
```
Agar file ka naam `my_bot_v2.py` hai to:
`stop_123456_my_bot_v2.py` -> split('_',2) -> `['stop', '123456', 'my_bot_v2.py']` -> Thik hai is case me.
Par `approve_file_123_file_name_with_many_underscores` -> `data.split('_')` with no limit in approval function -> `data_parts = call.data.split('_')` -> 3 se zyada parts -> Tum `'_'.join(data_parts[3:])` kar rahe ho, thik hai. Lekin `file_` control me theek hai. Fir bhi Telegram callback_data ki limit 64 bytes hai. Lamba filename > 64 bytes fail karega.

#### D) Pending Files Memory Me Hain
`pending_zip_files = {}` RAM me hai. Bot restart hote hi saare approval pending files gayab. User ko dubara upload karna padega. Isko disk ya DB me store karna chahiye tha.

#### E) Flask Keep Alive + Polling - Race Condition
`keep_alive()` daemon thread me Flask chalata hai, aur main thread me `infinity_polling`. Koyeb/Render pe thik hai, par cleanup `atexit` Flask thread ko kill nahi karta, zombie processes reh jayenge.

---

### 4. Functional Bugs / Logic Issues

1.  **Duplicate Dangerous List:** Same 300+ pattern list 2 jagah copy-paste hai (`check_code_security` aur `scan_zip_security`). Maintain karna impossible.

2.  **File Forward to Owner:** Har file `bot.forward_message(OWNER_ID, ...)` se owner ko forward hoti hai. Privacy issue aur API limit lag sakti hai.

3.  **Limit Check Before Security:** Tum pehle file limit check karte ho, fir security. Agar user limit full hai aur dangerous file bhejta hai to bhi owner ko security alert nahi jayega.

4.  **SQL Injection nahi par DB Lock Issue:** Har DB operation me `DB_LOCK` use kiya hai, good. Par `sqlite3.connect(check_same_thread=False)` har function me new connection - thik hai but connection pooling better hota.

5.  **Typo:** Flask route pe `POWRED-BY` likha hai, `POWERED-BY` hona chahiye.

6.  **`skill required in ...?`** - `FREE_USER_LIMIT = 2` okay hai par `/managechannels` me channel add karte time `bot.get_chat()` fail ho sakta hai agar bot us channel me nahi hai.

7.  **Broadcast Media Support Adha hai:** `execute_broadcast` me sirf text/photo/video ka hi support hai, document/audio support nahi dala jabki `process_broadcast_message` me check hai.

8.  **Potential Path Traversal Fixed hai but Weak:** `member_path.startswith(abspath(temp_dir))` check achha hai, par `..` wala file name ko normalize karna chahiye.

### 5. Achhi Cheezein (Good Parts)

- `get_env()` me proper error handling hai
- `psutil` se process tree kill karna achha implementation hai
- Mandatory channel join ka system solid hai
- User management (ban, custom limit, logs) kaafi detailed hai
- Temp dir cleanup `finally` me kiya hai

### 6. Kya Fix Karna Chahiye? Recommendations

**Immediate Fix:**

1.  **Security Filter Badlo:** Ye wala list hatao. Sirf ye block karo:
    ```python
    truly_dangerous = [
        r'os\.system', r'subprocess\.', r'__import__', 
        r'eval\s*\(', r'exec\s*\(', r'(shutil\.rmtree|os\.remove)',
        r'/etc/passwd', r'.ssh/id_rsa'
    ]
    ```
    Aur better - har user ko alag Docker container me chalao.

2.  **File Name Hash Karo:** Callback me filename mat bhejo, file_id ya short hash bhejo.

3.  **Pending Files ko Disk pe Save Karo:** `pending_zips/` folder banao.

4.  **Resource Limit:** `psutil` se CPU/Memory limit lagao, aur max 1 script per free user.

5.  **`.re` aur `.telebot` jaise common patterns hatao.**

Agar chaho to main iska FIXED VERSION bana sakta hoon:
- Clean security checker
- Docker-less but safe sandbox
- Fixed callback parser
- Optimized DB aur better file handling

Bolo, kya chahiye?
- `Fixed main.py`
- Sirf security filter ka sahi version?
- Ya isko production-ready Docker version me convert kar du?
