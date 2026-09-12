DISCORD BOT 2024 + AFK - WISPBYTE

ไฟล์นี้พัฒนาต่อจาก discord_bot2024-main

Startup:
    python main.py

Environment Variables:
    TOKEN=BOT_TOKEN
    GUILD_ID=SERVER_ID

หรือใช้:
    DISCORD_TOKEN=BOT_TOKEN

ติดตั้ง:
    pip install -r requirements.txt

คำสั่งใหม่:
    /afk
        เลือก Voice Channel แล้วบอทจะเข้าไป AFK

    /off
        ปิด AFK และนำบอทออกจากห้อง

ระบบ AFK:
- self mute
- self deafen
- reconnect เมื่อหลุด
- จำห้อง AFK ของแต่ละเซิร์ฟเวอร์ระหว่างที่ process ยังทำงาน
- ทำงานต่อเนื่องตราบใดที่ WispByte ยังรัน Python process

หมายเหตุ:
- Slash command ชื่อใน Discord ต้องเป็นตัวพิมพ์เล็ก จึงใช้ /afk และ /off
- GUILD_ID ช่วยให้คำสั่ง Slash ซิงก์กับเซิร์ฟเวอร์ได้เร็ว
- ถ้าใช้ on_member_join/on_member_remove และ on_message ของโค้ดเดิม
  ให้เปิด Privileged Gateway Intents ที่ Discord Developer Portal:
  Server Members Intent
  Message Content Intent
- Bot ต้องมี View Channel และ Connect ใน Voice Channel
- หาก TOKEN เคยถูกเผยแพร่ ให้ Reset Token ก่อนใช้
