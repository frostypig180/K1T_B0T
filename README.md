# How to Use K1TB0T
Author: Eli Gruhlke, Caleb Schweigert, and Will Dani
Last Updated: 04/16/26

## How to Run K1TB0T
1. open terminal "Ctrl+Alt+T"
2. navigate to Documents/k1tbot
3. run "./run.sh"
4. open web browser and go to "https://k1tb0t.com"
5. for admin access, visit k1tb0t.com/admin and enter the user and pass

## How to Create a .json file to Use KitBot for Another Class
1. right click "class_configs"
2. click "New File..."
3. name the file whatever the class is called and append ".json" to it
4. within the new class file, include this information:
    {   
    	"class_id": "CLASS CODE",
    	"class_name": "CLASS NAME"
    }
5. replace CLASS CODE and CLASS NAME with the actual information

## How to Upload and Delete Resources
1. In admin panel, go to "Upload New Resource" make sure to select a file before uploading
2. to delete a file, go to "Delete Resource" and check which file you want to remove
3. ***IMPORTANT*** select ok for the 2 following prompts when deleting but ***DO NOT*** check the box "Don't allow localhost:5500 to prompt you again"

## Cloudflare Tunnel Configuration
- cloudflare tunnel configuration files and cert files can be found at ~/.cloudflared

## How to Login to PostgreSQL
1. "psql -U k1tbot -d k1tbotdb -h localhost -W"
2. Password: "BotofK1t"


## Useful PostgreSQL Commands

\q to quit
"\x on" for better visibility of the table in bash
To view data within the DB use this example code:
    SELECT id, user_id, class_id, started_at
    FROM conversations
    ORDER BY started_at DESC
    LIMIT 10;
within the record table, pres "q" to leave the table