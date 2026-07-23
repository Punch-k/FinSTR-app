# --------------------------------------------------------------------------
# Hosts an API for the MyShare Stock Market and Portfolio Management Service
# Hosts all MyShare webpages
#
# Author: Vlad Litvak
# Email: vlitvak99@gmail.com
# Version: 4.2.9
# Since: 08.27.2020
# --------------------------------------------------------------------------

from flask import Flask, Response, render_template, request
from flask_restful import Api, Resource, reqparse
from passlib.hash import sha256_crypt
from yahoo_fin import stock_info
import yfinance
import secrets
import sqlite3
import re
import json
import yagmail
import os
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import send_from_directory
import time
from scripts.tickers import SCREENER_TICKERS
from scripts.fetch_data import fetch_insider_transactions, fetch_calendar

# Load credentials from environment variables (never hardcode secrets)
ADMIN_EMAIL_ADDRESS = os.environ.get('ADMIN_EMAIL_ADDRESS', '')
ADMIN_EMAIL_PASSWORD = os.environ.get('ADMIN_EMAIL_PASSWORD', '')

SQLITE_DATABASE = "database/MyShare.db"

SCREENER_CACHE_TTL = 6 * 3600  # seconds

MAX_USER_ID = 1000000000000
MAX_ID = 1000000000000000

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_FORBIDDEN = 403
HTTP_CONFLICT = 409
HTTP_INTERNAL_SERVER_ERROR = 500

AUTHENTICATION_FAILED = 1000
OWNERSHIP_AUTHENTICATION_FAILED = 1001
TOO_MANY_AUTHENTICATION_ATTEMPTS = 1002

BOUGHT_SOLD_DISCREPANCY = 2000

MISSING_REQUIRED_PARAMS = 10000
MISSING_ID_PARAM = 10001
MISSING_PASSWORD_PARAM = 10002
MISSING_NEW_PASSWORD_PARAM = 10003
MISSING_RESET_CODE_PARAM = 10004
MISSING_USERNAME_PARAM = 10005
MISSING_EMAIL_PARAM = 10006
MISSING_FIRST_NAME_PARAM = 10007
MISSING_LAST_NAME_PARAM = 10008
MISSING_SYMBOL_PARAM = 10009
MISSING_SHARES_PARAM = 10010
MISSING_BUY_PRICE_PARAM = 10011
MISSING_BUY_DATE_PARAM = 10012
MISSING_SELL_PRICE_PARAM = 10013
MISSING_SELL_DATE_PARAM = 10014
MISSING_LOT_ID_PARAM = 10015
MISSING_SELL_LOT_ID_PARAM = 10016

INVALID_PASSWORD_PARAM = 20002
INVALID_NEW_PASSWORD_PARAM = 20003
INVALID_USERNAME_PARAM = 20005
INVALID_EMAIL_PARAM = 20006
INVALID_FIRST_NAME_PARAM = 20007
INVALID_LAST_NAME_PARAM = 20008
INVALID_SYMBOL_PARAM = 20009
INVALID_SHARES_PARAM = 20010
INVALID_BUY_PRICE_PARAM = 20011
INVALID_BUY_DATE_PARAM = 20012
INVALID_SELL_PRICE_PARAM = 20013
INVALID_SELL_DATE_PARAM = 20014

USERNAME_ALREADY_TAKEN = 30005
EMAIL_ALREADY_TAKEN = 30006

# email must be in valid format (xxx@xxx.xxx) and can't be longer than 100 characters
emailRegex = r'^(?=.{1,100}$)[a-z0-9]+[\._]?[a-z0-9]+[@]\w+[.]\w{2,3}$'
def checkEmailFormat(email):
    return re.search(emailRegex, email)

# username can only contain letters, numbers, periods and underscores and must be 6-25 characters long
usernameRegex = r'^(?=.{6,25}$)[a-zA-Z0-9._]+$'
def checkUsernameFormat(username):
    return re.search(usernameRegex, username)

# password must be between 8 and 50 characters
def checkPasswordFormat(password):
    return len(password) >= 8 and len(password) <= 50

# name can't be longer than 25 characters and can only contain letters, periods, and spaces
nameRegex = r'^(?=.{1,25}$)[a-zA-Z.( )]+$'
def checkNameFormat(name):
    return re.search(nameRegex, name)

# must be a positive integer or a positive float with exactly one or two digits after the decimal
dollarRegex = r'^\d+\.\d\d$|^\d+\.\d$|^\d+$'
def checkDollarFormat(dollar):
    return re.search(dollarRegex, dollar)

# must be a valid date in the format YYYY-MM-DD
dateRegex = r'^\d\d\d\d[- /.](0[1-9]|1[012])[- /.](0[1-9]|[12][0-9]|3[01])$'
def checkDateFormat(date):
    return re.search(dateRegex, date)

# must be a positive integer
intRegex = r'^\d+$'
def checkIntFormat(integer):
    return re.search(intRegex, integer)

# must be 1 to 5 letters
symbolRegex = r'^(?=.{1,5}$)[a-zA-Z]+$'
def checkSymbolFormat(symbol):
    return re.search(symbolRegex, symbol)

# Database helper functions using parameterized queries to prevent SQL injection
def getDbConnection():
    """Get a database connection with foreign keys enabled."""
    connection = sqlite3.connect(SQLITE_DATABASE)
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection

def executeDatabaseQuery(query, params=()):
    """Execute a SELECT query with parameterized values."""
    connection = getDbConnection()
    cursor = connection.cursor()
    results = cursor.execute(query, params).fetchall()
    connection.close()
    return results

def executeDatabaseUpdate(statement, params=()):
    """Execute an UPDATE/INSERT/DELETE statement with parameterized values."""
    connection = getDbConnection()
    cursor = connection.cursor()
    cursor.execute(statement, params)
    connection.commit()
    rows_affected = cursor.rowcount
    connection.close()
    return rows_affected

def usernameAvailable(username):
    if not checkUsernameFormat(username):
        return False
    query = "SELECT COUNT(*) FROM Users WHERE Username = ?;"
    result = executeDatabaseQuery(query, (username,))
    return result[0][0] == 0

def userOwnsUsername(id, username):
    query = "SELECT COUNT(*) FROM Users WHERE ID = ? AND Username = ?;"
    result = executeDatabaseQuery(query, (id, username))
    return result[0][0] != 0

def emailAvailable(email):
    if not checkEmailFormat(email):
        return False
    query = "SELECT COUNT(*) FROM Users WHERE Email = ?;"
    result = executeDatabaseQuery(query, (email,))
    return result[0][0] == 0

def userOwnsEmail(id, email):
    query = "SELECT COUNT(*) FROM Users WHERE ID = ? AND Email = ?;"
    result = executeDatabaseQuery(query, (id, email))
    return result[0][0] != 0

def matchIdAndPassword(id, password):
    if not checkIntFormat(id):
        return False
    query = "SELECT Password FROM Users WHERE ID = ?;"
    result = executeDatabaseQuery(query, (id,))
    if result == []:
        return False
    return sha256_crypt.verify(password, result[0][0])

def symbolHeldByUser(symbol, userId):
    if not symbolExists(symbol):
        return False
    query = "SELECT COUNT(*) FROM Holdings WHERE User = ? AND Symbol = ? AND SellLotID IS NULL;"
    result = executeDatabaseQuery(query, (userId, symbol))
    return result[0][0] != 0

def lotOwnedByUser(lotId, userId):
    if not checkIntFormat(lotId) or not checkIntFormat(userId):
        return False
    query = "SELECT COUNT(*) FROM Holdings WHERE LotID = ? AND User = ?;"
    result = executeDatabaseQuery(query, (lotId, userId))
    return result[0][0] != 0

def lotHeldByUser(lotId, userId):
    if not checkIntFormat(lotId) or not checkIntFormat(userId):
        return False
    query = "SELECT COUNT(*) FROM Holdings WHERE LotID = ? AND User = ? AND SellLotID IS NULL;"
    result = executeDatabaseQuery(query, (lotId, userId))
    return result[0][0] != 0

def lotSoldByUser(sellLotId, userId):
    if not checkIntFormat(sellLotId) or not checkIntFormat(userId):
        return False
    query = "SELECT COUNT(*) FROM Holdings WHERE SellLotID = ? AND User = ?;"
    result = executeDatabaseQuery(query, (sellLotId, userId))
    return result[0][0] != 0

def shareHeldByUser(shareId, userId):
    if not checkIntFormat(shareId) or not checkIntFormat(userId):
        return False
    query = "SELECT COUNT(*) FROM Holdings WHERE ShareID = ? AND User = ? AND SellLotID IS NULL;"
    result = executeDatabaseQuery(query, (shareId, userId))
    return result[0][0] != 0

def shareSoldByUser(shareId, userId):
    if not checkIntFormat(shareId) or not checkIntFormat(userId):
        return False
    query = "SELECT COUNT(*) FROM Holdings WHERE ShareID = ? AND User = ? AND SellLotID IS NOT NULL;"
    result = executeDatabaseQuery(query, (shareId, userId))
    return result[0][0] != 0

def symbolExists(symbol):
    if not checkSymbolFormat(symbol):
        return False
    try:
        price = stock_info.get_live_price(symbol)
        info = yfinance.Ticker(symbol)
        info.info.update({ "currentPrice" : price })
    except:
        return False
    return True

def createUserId():
    while True:
        id = secrets.randbelow(MAX_USER_ID)
        query = "SELECT COUNT(*) FROM Users WHERE ID = ?;"
        result = executeDatabaseQuery(query, (id,))
        if result[0][0] == 0:
            return id

def createShareId():
    while True:
        id = secrets.randbelow(MAX_ID)
        query = "SELECT COUNT(*) FROM Holdings WHERE ID = ?;"
        result = executeDatabaseQuery(query, (id,))
        if result[0][0] == 0:
            return id

def createShareIds(numberOfShares):
    shareIds = []
    for i in range(numberOfShares):
        ShareIdUnique = False
        while not ShareIdUnique:
            shareId = secrets.randbelow(MAX_ID)
            query = "SELECT COUNT(*) FROM Holdings WHERE ShareID = ?;"
            result = executeDatabaseQuery(query, (shareId,))
            if result[0][0] == 0 and shareId not in shareIds:
                shareIds.append(shareId)
                ShareIdUnique = True
    return shareIds

def createLotId():
    while True:
        id = secrets.randbelow(MAX_ID)
        query = "SELECT COUNT(*) FROM Holdings WHERE LotID = ?;"
        result = executeDatabaseQuery(query, (id,))
        if result[0][0] == 0:
            return id

def createSellLotId():
    while True:
        id = secrets.randbelow(MAX_ID)
        query = "SELECT COUNT(*) FROM Holdings WHERE SellLotID = ?;"
        result = executeDatabaseQuery(query, (id,))
        if result[0][0] == 0:
            return id

def createPasswordResetEmail(firstName, username, resetCode):
    return f"""
            <!DOCTYPE html>
            <html xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
            <link rel="stylesheet" href="https://fonts.googleapis.com/css?family=Poppins">
            <body style="background-color:#191919;font-size:16px;font-family:'Poppins', 'Helvetica Neue', sans-serif;">
                <div style="background-color:#464646;background:linear-gradient(0deg, #232323 0%, #5A5A5A 100%);Margin:0px auto;max-width:600px;">
                    <div style="color:#4f8cff;font-size:25px;width:40%;margin-left:30%;">
                        <a href="http://localhost:1817/myshare/home" style="text-decoration:none;">
                            <div style="width:100%;background-color: #353535;background: linear-gradient(0deg, #1D1D1D 0%, #353535 100%);color:#4f8cff;font-size:25px;text-align:center;padding-top:10px;padding-bottom:10px;border:2px solid #4f8cff;border-radius:10px;cursor:pointer;">📊 FinSTR</div>
                        </a>
                    </div>
                    <div style="width:90%;margin-left:5%;border-bottom:2px solid #4f8cff;">
                        <div style="color:#C4C4C4;width:90%;margin-left:5%;margin-bottom:15px;font-size:18px;font-weight:bolder;">Hi {firstName},</div>
                        <div style="color:#FFFDFD;width:90%;margin-left:5%;">You have requested to reset your FinSTR password. If this was not you, please ignore this email.</div>
                    </div>
                    <div style="width:90%;margin-left:5%;border-bottom:2px solid #4f8cff;">
                        <div style="color:#FFFDFD;width:90%;margin-left:5%;margin-bottom:5px;">Here is the information you will need to reset your password:</div>
                        <div style="color:#C4C4C4;width:90%;margin-left:5%;">Username: <span style="color:#FFFDFD;font-weight:bolder;">{username}</span><br><span>Password Reset Code: </span><span style="font-weight:bolder;color:#FF3200">{resetCode}</span></div>
                    </div>
                    <div style="width:36%;margin-left:32%;">
                        <a href="http://localhost:1817/myshare/recover-password" style="text-decoration:none;">
                            <div style="width:100%;margin-bottom:10px;padding-top:10px;padding-bottom:10px;background-color:#4f8cff;background:linear-gradient(90deg, #4f8cff 0%, #3d6fd9 100%);color:#FFFDFD;border-radius:45px;text-align:center;cursor:pointer;">Reset Password</div>
                        </a>
                    </div>
                </div>
            </body>
            </html>
            """


# ---------- SCREENER CACHE ----------
def init_screener_cache():
    conn = sqlite3.connect(SQLITE_DATABASE)
    conn.execute("""CREATE TABLE IF NOT EXISTS StockCache (
        Ticker TEXT PRIMARY KEY,
        Data TEXT NOT NULL,
        UpdatedAt INTEGER NOT NULL
    )""")
    conn.commit()
    conn.close()

def get_screener_cache(ticker):
    try:
        conn = sqlite3.connect(SQLITE_DATABASE)
        row = conn.execute("SELECT Data, UpdatedAt FROM StockCache WHERE Ticker=?", (ticker,)).fetchone()
        conn.close()
        if row and (time.time() - row[1]) < SCREENER_CACHE_TTL:
            return json.loads(row[0])
    except Exception:
        pass
    return None

def set_screener_cache(ticker, data):
    try:
        conn = sqlite3.connect(SQLITE_DATABASE)
        conn.execute("INSERT OR REPLACE INTO StockCache (Ticker, Data, UpdatedAt) VALUES (?,?,?)",
                     (ticker, json.dumps(data), int(time.time())))
        conn.commit()
        conn.close()
    except Exception:
        pass

def fetch_ticker_data(ticker):
    cached = get_screener_cache(ticker)
    if cached:
        return cached
    try:
        t = yfinance.Ticker(ticker)
        info = t.info
        hist = t.history(period="60d")
        price = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
        prev = float(info.get("previousClose") or price)
        chg = round(((price - prev) / prev * 100), 2) if prev else 0
        n = min(50, len(hist))
        ma50 = round(float(hist["Close"].tail(n).mean()), 2) if n >= 5 else round(price, 2)
        data = {
            "ticker": ticker,
            "name": info.get("shortName") or info.get("longName") or ticker,
            "sector": info.get("sector") or "Unknown",
            "industry": info.get("industry") or "Unknown",
            "marketCap": round((info.get("marketCap") or 0) / 1e9, 1),
            "price": round(price, 2),
            "changePercent": chg,
            "pe": round(float(info.get("trailingPE") or 0), 1),
            "forwardPE": round(float(info.get("forwardPE") or 0), 1),
            "peg": round(float(info.get("pegRatio") or 0), 2),
            "ma50": ma50,
            "eps": round(float(info.get("trailingEps") or 0), 2),
            "dividendYield": round(float(info.get("dividendYield") or 0) * 100, 2),
            "beta": round(float(info.get("beta") or 1), 2),
            "roe": round(float(info.get("returnOnEquity") or 0) * 100, 1),
            "profitMargin": round(float(info.get("profitMargins") or 0) * 100, 1),
            "volume": round((info.get("volume") or 0) / 1e6, 2),
            "avgVolume": round((info.get("averageVolume") or 0) / 1e6, 2),
            "insiderTransactions": fetch_insider_transactions(ticker),
            **fetch_calendar(ticker),
        }
        set_screener_cache(ticker, data)
        return data
    except Exception:
        return None


# /myshare/info
class Info(Resource):

    # get general information of a symbol including current price, previous close, name, business summary, etc.
    #
    # param : "symbol" the symbol whose info will be returned (required)
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument("symbol")
        params = parser.parse_args()

        if params["symbol"] is None:
            return Response(json.dumps({ "error" : MISSING_SYMBOL_PARAM, "message" : "'symbol' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        try:
            price = round(stock_info.get_live_price(params["symbol"]), 2)
            info = yfinance.Ticker(params["symbol"])
            info.info.update({ "currentPrice" : price })
            return Response(json.dumps(info.info), status=HTTP_OK, mimetype="application/json")
        except:
            return Response(json.dumps({ "error" : INVALID_SYMBOL_PARAM, "message" : "Invalid symbol" }), status=HTTP_BAD_REQUEST, mimetype="application/json")


# /myshare/info/price
class Price(Resource):

    # get current price a symbol
    #
    # param : "symbol" the symbol whose price will be returned (required)
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument("symbol")
        params = parser.parse_args()

        if params["symbol"] is None:
            return Response(json.dumps({ "error" : MISSING_SYMBOL_PARAM, "message" : "'symbol' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        try:
            return Response(json.dumps({ "price" : round(stock_info.get_live_price(params["symbol"]), 2) }), status=HTTP_OK, mimetype="application/json")
        except:
            return Response(json.dumps({ "error" : INVALID_SYMBOL_PARAM, "message" : "Invalid symbol" }), status=HTTP_BAD_REQUEST, mimetype="application/json")


# /myshare/user/id
class ID(Resource):

    # get a user's id
    #
    # param : "username" the user's username (required)
    # param : "password" the user's password (required)
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument("username")
        parser.add_argument("password")
        params = parser.parse_args()

        if params["username"] is None:
            return Response(json.dumps({ "error" : MISSING_USERNAME_PARAM, "message" : "'username' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["password"] is None:
            return Response(json.dumps({ "error" : MISSING_PASSWORD_PARAM, "message" : "'password' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkUsernameFormat(params["username"]) or not checkPasswordFormat(params["password"]):
            return Response(json.dumps({ "id" : None }), status=HTTP_OK, mimetype="application/json")

        query = "SELECT Password, ID FROM Users WHERE Username = ?;"
        result = executeDatabaseQuery(query, (params["username"],))
        if result == []:
            return Response(json.dumps({ "id" : None }), status=HTTP_OK, mimetype="application/json")

        if sha256_crypt.verify(params["password"], result[0][0]):
            return Response(json.dumps({ "id" : result[0][1] }), status=HTTP_OK, mimetype="application/json")

        return Response(json.dumps({ "id" : None }), status=HTTP_OK, mimetype="application/json")


# /myshare/user/
class User(Resource):

    # get a user's username, email, first name, and last name
    #
    # param : "id" the user's id (required)
    # param : "password" the user's password (required)
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument("id")
        parser.add_argument("password")
        params = parser.parse_args()

        if params["id"] is None:
            return Response(json.dumps({ "error" : MISSING_ID_PARAM, "message" : "'id' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["password"] is None:
            return Response(json.dumps({ "error" : MISSING_PASSWORD_PARAM, "message" : "'password' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkIntFormat(params["id"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")
        if not checkPasswordFormat(params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        query = "SELECT Password, Username, Email, FirstName, LastName FROM Users WHERE ID = ?;"
        result = executeDatabaseQuery(query, (params["id"],))
        if result == []:
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if sha256_crypt.verify(params["password"], result[0][0]):
            return Response(json.dumps({ "username" : result[0][1], "email" : result[0][2], "firstName" : result[0][3], "lastName" : result[0][4] }), status=HTTP_OK, mimetype="application/json")

        return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")


    # register a user
    #
    # param : "username" the user's username (required)
    # param : "password" the user's password (required)
    # param : "email" the user's email (required)
    # param : "firstName" the user's first name (required)
    # param : "lastName" the user's last name (required)
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument("username")
        parser.add_argument("password")
        parser.add_argument("email")
        parser.add_argument("firstName")
        parser.add_argument("lastName")
        params = parser.parse_args()

        if params["username"] is None:
            return Response(json.dumps({ "error" : MISSING_USERNAME_PARAM, "message" : "'username' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["password"] is None:
            return Response(json.dumps({ "error" : MISSING_PASSWORD_PARAM, "message" : "'password' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["email"] is None:
            return Response(json.dumps({ "error" : MISSING_EMAIL_PARAM, "message" : "'email' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["firstName"] is None:
            return Response(json.dumps({ "error" : MISSING_FIRST_NAME_PARAM, "message" : "'firstName' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["lastName"] is None:
            return Response(json.dumps({ "error" : MISSING_LAST_NAME_PARAM, "message" : "'lastName' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkUsernameFormat(params["username"]):
            return Response(json.dumps({ "error" : INVALID_USERNAME_PARAM, "message" : "Invalid username" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkPasswordFormat(params["password"]):
            return Response(json.dumps({ "error" : INVALID_PASSWORD_PARAM, "message" : "Invalid Password" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkEmailFormat(params["email"]):
            return Response(json.dumps({ "error" : INVALID_EMAIL_PARAM, "message" : "Invalid email" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkNameFormat(params["firstName"]):
            return Response(json.dumps({ "error" : INVALID_FIRST_NAME_PARAM, "message" : "Invalid first name" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkNameFormat(params["lastName"]):
            return Response(json.dumps({ "error" : INVALID_LAST_NAME_PARAM, "message" : "Invalid last name" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not usernameAvailable(params["username"]):
            return Response(json.dumps({ "error" : USERNAME_ALREADY_TAKEN, "message" : "Username is already taken" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not emailAvailable(params["email"]):
            return Response(json.dumps({ "error" : EMAIL_ALREADY_TAKEN, "message" : "Email is already taken" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        id = createUserId()
        password = sha256_crypt.encrypt(params["password"])
        statement = "INSERT OR IGNORE INTO Users (ID, Username, Password, Email, FirstName, LastName) VALUES (?, ?, ?, ?, ?, ?);"
        update = executeDatabaseUpdate(statement, (id, params["username"], password, params["email"], params["firstName"], params["lastName"]))
        if update == 0:
            return Response(json.dumps({ "message" : "Internal Server Error" }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")
        return Response(json.dumps({ "id" : id }), status=HTTP_OK, mimetype="application/json")


    # edit a user's information
    #
    # param : "id" the user's id (required)
    # param : "password" the user's password (required)
    # param : "username" the user's new username (*)
    # param : "newPassword" the user's new password (*)
    # param : "email" the user's new email (*)
    # param : "firstName" the user's new first name (*)
    # param : "lastName" the user's new last name (*)
    #
    # (*) at least one of these parameters is required
    def patch(self):
        parser = reqparse.RequestParser()
        parser.add_argument("id")
        parser.add_argument("password")
        parser.add_argument("username")
        parser.add_argument("newPassword")
        parser.add_argument("email")
        parser.add_argument("firstName")
        parser.add_argument("lastName")
        params = parser.parse_args()

        if params["id"] is None:
            return Response(json.dumps({ "error" : MISSING_ID_PARAM, "message" : "'id' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["password"] is None:
            return Response(json.dumps({ "error" : MISSING_PASSWORD_PARAM, "message" : "'password' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkIntFormat(params["id"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not checkPasswordFormat(params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not matchIdAndPassword(params["id"], params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        updates = []
        update_params = []

        if params["username"] is not None:
            if not checkUsernameFormat(params["username"]):
                return Response(json.dumps({ "error" : INVALID_USERNAME_PARAM, "message" : "Invalid username" }), status=HTTP_BAD_REQUEST, mimetype="application/json")
            updates.append("Username = ?")
            update_params.append(params["username"])

        if params["email"] is not None:
            if not checkEmailFormat(params["email"]):
                return Response(json.dumps({ "error" : INVALID_EMAIL_PARAM, "message" : "Invalid email" }), status=HTTP_BAD_REQUEST, mimetype="application/json")
            updates.append("Email = ?")
            update_params.append(params["email"])

        if params["newPassword"] is not None:
            if not checkPasswordFormat(params["newPassword"]):
                return Response(json.dumps({ "error" : INVALID_NEW_PASSWORD_PARAM, "message" : "Invalid new password" }), status=HTTP_BAD_REQUEST, mimetype="application/json")
            updates.append("Password = ?")
            update_params.append(sha256_crypt.encrypt(params["newPassword"]))

        if params["firstName"] is not None:
            if not checkNameFormat(params["firstName"]):
                return Response(json.dumps({ "error" : INVALID_FIRST_NAME_PARAM, "message" : "Invalid first name" }), status=HTTP_BAD_REQUEST, mimetype="application/json")
            updates.append("FirstName = ?")
            update_params.append(params["firstName"])

        if params["lastName"] is not None:
            if not checkNameFormat(params["lastName"]):
                return Response(json.dumps({ "error" : INVALID_LAST_NAME_PARAM, "message" : "Invalid last name" }), status=HTTP_BAD_REQUEST, mimetype="application/json")
            updates.append("LastName = ?")
            update_params.append(params["lastName"])

        if params["username"] is not None:
            if not usernameAvailable(params["username"]) and not userOwnsUsername(params["id"], params["username"]):
                return Response(json.dumps({ "error" : USERNAME_ALREADY_TAKEN, "message" : "Username is already taken" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if params["email"] is not None:
            if not emailAvailable(params["email"]) and not userOwnsEmail(params["id"], params["email"]):
                return Response(json.dumps({ "error" : EMAIL_ALREADY_TAKEN, "message" : "Email is already taken" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if len(updates) == 0:
            return Response(json.dumps({ "error" : MISSING_REQUIRED_PARAMS, "message" : "'username' and/or 'newPassword' and/or 'email' and/or 'firstName' and/or 'lastName' parameter(s) required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        update_params.append(params["id"])
        statement = f"UPDATE Users SET {', '.join(updates)} WHERE ID = ?;"
        rows_affected = executeDatabaseUpdate(statement, tuple(update_params))
        if rows_affected == 0:
            return Response(json.dumps({ "message" : "Internal Server Error" }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")

        query = "SELECT Username, Email, FirstName, LastName FROM Users WHERE ID = ?;"
        result = executeDatabaseQuery(query, (params["id"],))

        if result == []:
            return Response(json.dumps({ "message" : "Internal Server Error" }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")

        return Response(json.dumps({ "username" : result[0][0], "email" : result[0][1], "firstName" : result[0][2], "lastName" : result[0][3] }), status=HTTP_OK, mimetype="application/json")


    # remove a user and all of their data
    #
    # param : "id" the user's id (required)
    # param : "password" the user's password (required)
    def delete(self):
        parser = reqparse.RequestParser()
        parser.add_argument("id")
        parser.add_argument("password")
        params = parser.parse_args()

        if params["id"] is None:
            return Response(json.dumps({ "error" : MISSING_ID_PARAM, "message" : "'id' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["password"] is None:
            return Response(json.dumps({ "error" : MISSING_PASSWORD_PARAM, "message" : "'password' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkIntFormat(params["id"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not checkPasswordFormat(params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not matchIdAndPassword(params["id"], params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        statement = "DELETE FROM Users WHERE ID = ?;"
        rows_affected = executeDatabaseUpdate(statement, (params["id"],))
        if rows_affected == 0:
            return Response(json.dumps({ "message" : "Internal Server Error" }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")

        return Response(json.dumps({ "id" : params["id"] }), status=HTTP_OK, mimetype="application/json")


# /myshare/user/holdings
class Holdings(Resource):

    # get a user's holdings
    #
    # param : "id" the user's id (required)
    # param : "password" the user's password (required)
    # param : "symbol" a filter to return holdings of only a specific symbol
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument("id")
        parser.add_argument("password")
        parser.add_argument("symbol")
        params = parser.parse_args()

        if params["id"] is None:
            return Response(json.dumps({ "error" : MISSING_ID_PARAM, "message" : "'id' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["password"] is None:
            return Response(json.dumps({ "error" : MISSING_PASSWORD_PARAM, "message" : "'password' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkIntFormat(params["id"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not checkPasswordFormat(params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not matchIdAndPassword(params["id"], params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        symbolFilter = ""
        symbol_param = None
        if params["symbol"] is not None:
            if not checkSymbolFormat(params["symbol"]) or not symbolExists(params["symbol"]):
                return Response(json.dumps({ "error" : INVALID_SYMBOL_PARAM, "message" : "Invalid symbol" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

            if not symbolHeldByUser(params["symbol"], params["id"]):
                return Response(json.dumps({ "symbol" : params["symbol"].upper(), "shares" : 0, "currentPrice" : round(stock_info.get_live_price(params["symbol"]), 2), "avgCostPerShare" : None, "totalPrincipal" : 0.0, "marketValue" : 0.0, "valueIncrease" : 0.0, "lots" : [] }), status=HTTP_OK, mimetype="application/json")

            symbolFilter = "AND Symbol = ?"
            symbol_param = params["symbol"].upper()

        holdings = []
        totalPrincipal = 0.0
        totalValueIncrease = 0.0
        symbol = None
        currentPrice = None
        lot = None
        lots = None
        shares = None
        buyPrice = None
        buyDate = None
        symbolShares = None
        symbolPrincipal = None

        query = f"SELECT Symbol, LotID, ShareID, BuyPrice, BuyDate FROM Holdings WHERE User = ? {symbolFilter} AND SellLotID IS NULL ORDER BY Symbol, BuyDate DESC, LotID;"
        if symbol_param:
            result = executeDatabaseQuery(query, (params["id"], symbol_param))
        else:
            result = executeDatabaseQuery(query, (params["id"],))

        for row in result:
            # for the first returned row, save the row's information
            if symbol is None:
                symbol = row[0]
                currentPrice = round(stock_info.get_live_price(symbol), 2)
                lot = int(row[1])
                lots = []
                shares = [int(row[2])]
                buyPrice = round(float(row[3]), 2)
                buyDate = row[4]
                symbolShares = 1
                symbolPrincipal = buyPrice

            # if the symbol is different than the current symbol, add the current lot to lots,
            # then add lots to holdings, then save this row's information
            elif symbol != row[0]:
                lots.append({ "lotId" : lot, "buyPrice" : buyPrice, "buyDate" : buyDate, "valueIncrease": round((currentPrice - buyPrice) * len(shares), 2), "shares" : len(shares), "shareIds" : shares })
                info = yfinance.Ticker(symbol)
                info.info.update({ "currentPrice" : currentPrice })
                holdings.append({ "symbol" : symbol, "shares" : symbolShares, "avgCostPerShare" : round(symbolPrincipal / symbolShares, 2), "principal" : round(symbolPrincipal, 2), "marketValue" : round(currentPrice * symbolShares, 2), "valueIncrease" : round((currentPrice * symbolShares) - symbolPrincipal, 2), "lots" : lots, "info" : info.info })
                totalValueIncrease += round((currentPrice * symbolShares) - symbolPrincipal, 2)
                totalPrincipal += round(symbolPrincipal, 2)
                symbol = row[0]
                currentPrice = round(stock_info.get_live_price(symbol), 2)
                lot = int(row[1])
                lots = []
                shares = [int(row[2])]
                buyPrice = round(float(row[3]), 2)
                buyDate = row[4]
                symbolShares = 1
                symbolPrincipal = buyPrice

            # if only the lot is different than the current lot, add the current lot to lots,
            # then save this this lot
            elif lot != int(row[1]):
                lots.append({ "lotId" : lot, "buyPrice" : buyPrice, "buyDate" : buyDate, "valueIncrease": round((currentPrice - buyPrice) * len(shares), 2), "shares" : len(shares), "shareIds" : shares })
                lot = int(row[1])
                shares = [int(row[2])]
                buyPrice = round(float(row[3]), 2)
                buyDate = row[4]
                symbolShares += 1
                symbolPrincipal += buyPrice

            # otherwise, add this share to shares
            else:
                symbolShares += 1
                symbolPrincipal += buyPrice
                shares.append(int(row[2]))

        # add the saved information from the last returned row to lots and holdings
        if symbol is not None:
            lots.append({ "lotId" : lot, "buyPrice" : buyPrice, "buyDate" : buyDate, "valueIncrease": round((currentPrice - buyPrice) * len(shares), 2), "shares" : len(shares), "shareIds" : shares })
            info = yfinance.Ticker(symbol)
            info.info.update({ "currentPrice" : currentPrice })
            holdings.append({ "symbol" : symbol, "shares" : symbolShares, "avgCostPerShare" : round(symbolPrincipal / symbolShares, 2), "principal" : round(symbolPrincipal, 2), "marketValue" : round(currentPrice * symbolShares, 2), "valueIncrease" : round((currentPrice * symbolShares) - symbolPrincipal, 2), "lots" : lots, "info" : info.info })
            totalValueIncrease += round((currentPrice * symbolShares) - symbolPrincipal, 2)
            totalPrincipal += round(symbolPrincipal, 2)

        if params["symbol"] is not None:
            return Response(json.dumps(holdings[0]), status=HTTP_OK, mimetype="application/json")

        return Response(json.dumps({ "id" : int(params["id"]), "totalPrincipal" : round(totalPrincipal, 2), "marketValue": round(totalPrincipal + totalValueIncrease, 2), "totalValueIncrease" : totalValueIncrease, "holdings" : holdings }), status=HTTP_OK, mimetype="application/json")


# /myshare/user/lots
class Lots(Resource):

    # get a user's lots
    #
    # param : "id" the user's id (required)
    # param : "password" the user's password (required)
    # param : "lotId" a filter to return info of only a specific lot
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument("id")
        parser.add_argument("password")
        parser.add_argument("lotId")
        params = parser.parse_args()

        if params["id"] is None:
            return Response(json.dumps({ "error" : MISSING_ID_PARAM, "message" : "'id' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["password"] is None:
            return Response(json.dumps({ "error" : MISSING_PASSWORD_PARAM, "message" : "'password' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkIntFormat(params["id"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not checkPasswordFormat(params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not matchIdAndPassword(params["id"], params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        lotFilter = ""
        lot_params = []
        if params["lotId"] is not None:
            if not lotOwnedByUser(params["lotId"], params["id"]):
                return Response(json.dumps({ "error" : OWNERSHIP_AUTHENTICATION_FAILED, "message" : "User does not own this lot" }), status=HTTP_FORBIDDEN, mimetype="application/json")
            lotFilter = "LotID = ? AND "
            lot_params = [params["lotId"]]

        lots = []
        totalProfitFromSelling = 0.0
        totalValueIncrease = 0.0
        totalPrincipal = 0.0
        lot = None
        symbol = None
        buyPrice = None
        buyDate = None
        currentPrice = None
        sellLots = None
        holding = None
        sellLot = None
        sold = None
        profitFromSelling = None

        query = f"SELECT LotID, Symbol, ShareID, BuyPrice, BuyDate, SellLotID, SellPrice, SellDate FROM Holdings WHERE {lotFilter}User = ? ORDER BY BuyDate DESC, LotID, SellDate DESC, SellLotID;"
        result = executeDatabaseQuery(query, tuple(lot_params) + (params["id"],))

        for row in result:
            # for the first returned row, save the row's information
            if lot is None:
                lot = int(row[0])
                symbol = row[1]
                buyPrice = round(float(row[3]), 2)
                buyDate = row[4]
                currentPrice = round(stock_info.get_live_price(symbol), 2)
                holding = []
                sellLots = []
                sold = 0
                profitFromSelling = 0.0
                # if this share was not sold, add it to the holdings array
                if row[5] is None:
                    holding.append(int(row[2]))

                # otherwise initialize a sell lot and add this share to its shareIds
                else:
                    sellLot = { "sellLotId" : int(row[5]), "sellPrice" : round(float(row[6]), 2), "sellDate" : row[7], "shareIds" : [int(row[2])] }
                    sold += 1

            # if the lot is different than the previous row, add the current lot to lots,
            # then save this row's information
            elif lot != row[0]:
                # if there is a saved sell lot, add it to sellLots
                if sellLot is not None:
                    sellLotShares = len(sellLot["shareIds"])
                    sellLotProfit = (sellLot["sellPrice"] - buyPrice) * sellLotShares
                    profitFromSelling += sellLotProfit
                    sellLot.update({ "shares" : sellLotShares, "profit" : round(sellLotProfit, 2)} )
                    sellLots.append(sellLot)

                sharesHolding = len(holding)
                valueIncrease = round((currentPrice - buyPrice) * sharesHolding, 2)
                totalValueIncrease += valueIncrease
                totalPrincipal += round(buyPrice * sharesHolding, 2)
                totalProfitFromSelling += profitFromSelling
                lots.append({ "lotId" : lot, "symbol" : symbol, "buyPrice" : buyPrice, "buyDate" : buyDate, "currentPrice" : currentPrice, "profitFromSelling" : round(profitFromSelling, 2), "holdingValueIncrease" : valueIncrease, "sharesHolding" : sharesHolding, "holding" : holding, "sharesSold" : sold, "sellLots" : sellLots })
                lot = int(row[0])
                symbol = row[1]
                buyPrice = round(float(row[3]), 2)
                buyDate = row[4]
                currentPrice = round(stock_info.get_live_price(symbol), 2)
                holding = []
                sellLots = []
                sellLot = None
                sold = 0
                profitFromSelling = 0.0
                # if this share was not sold, add it to the holdings array
                if row[5] is None:
                    holding.append(int(row[2]))

                # otherwise initialize a sell lot and add this share to its shareIds
                else:
                    sellLot = { "sellLotId" : int(row[5]), "sellPrice" : round(float(row[6]), 2), "sellDate" : row[7], "shareIds" : [int(row[2])] }
                    sold += 1

            else:
                # otherwise if this share was not sold add it to the holding array
                if row[5] is None:
                    holding.append(int(row[2]))

                    # if there is a saved sell lot, add it to sellLots
                    if sellLot is not None:
                        sellLotShares = len(sellLot["shareIds"])
                        sellLotProfit = (sellLot["sellPrice"] - buyPrice) * sellLotShares
                        profitFromSelling += sellLotProfit
                        sellLot.update({ "shares" : sellLotShares, "profit" : round(sellLotProfit, 2) })
                        sellLots.append(sellLot)
                        sellLot = None

                elif sellLot is not None:
                    # otherwise if there is a saved sell lot and this share is from another lot,
                    # add the current sell lot to sellLots, then initialize a sell lot and add this share to its shareIds
                    if sellLot["sellLotId"] != int(row[5]):
                        sellLotShares = len(sellLot["shareIds"])
                        sellLotProfit = (sellLot["sellPrice"] - buyPrice) * sellLotShares
                        profitFromSelling += sellLotProfit
                        sellLot.update({ "shares" : sellLotShares, "profit" : sellLotProfit } )
                        sellLots.append(sellLot)
                        sellLot = { "sellLotId" : int(row[5]), "sellPrice" : round(float(row[6]), 2), "sellDate" : row[7], "shareIds" : [int(row[2])] }

                    # otherwise if there is a saved sell lot and this share is from that lot,
                    # add this share to the lot's shareIds
                    else:
                        sellLot["shareIds"].append(int(row[2]))
                    sold += 1

                # otherwise initialize a sell lot and add this share to its shareIds
                else:
                    sellLot = { "sellLotId" : int(row[5]), "sellPrice" : round(float(row[6]), 2), "sellDate" : row[7], "shareIds" : [int(row[2])] }
                    sold += 1

        # append the saved information from the last returned row to lots
        if lot is not None:

            # if there is a saved sell lot, add it to sellLots
            if sellLot is not None:
                sellLotShares = len(sellLot["shareIds"])
                sellLotProfit = (sellLot["sellPrice"] - buyPrice) * sellLotShares
                profitFromSelling += sellLotProfit
                sellLot.update({ "shares" : sellLotShares, "profit" : round(sellLotProfit, 2) })
                sellLots.append(sellLot)

            sharesHolding = len(holding)
            valueIncrease = round((currentPrice - buyPrice) * sharesHolding, 2)
            totalValueIncrease += valueIncrease
            totalPrincipal += round(buyPrice * sharesHolding, 2)
            totalProfitFromSelling += profitFromSelling
            lots.append({ "lotId" : lot, "symbol" : symbol, "buyPrice" : buyPrice, "buyDate" : buyDate, "currentPrice" : currentPrice, "profitFromSelling" : round(profitFromSelling, 2), "holdingValueIncrease" : valueIncrease, "sharesHolding" : sharesHolding, "holding" : holding, "sharesSold" : sold, "sellLots" : sellLots })

        if params["lotId"] is not None:
            return Response(json.dumps(lots[0]), status=HTTP_OK, mimetype="application/json")

        return Response(json.dumps({ "id" : int(params["id"]), "currentPrincipal" : totalPrincipal, "totalValueIncrease" : round(totalValueIncrease, 2), "totalProfitFromSelling": round(totalProfitFromSelling, 2), "lots" : lots }), status=HTTP_OK, mimetype="application/json")


    # add a new lot for a user
    #
    # param : "id" the user's id (required)
    # param : "password" the user's password (required)
    # param : "symbol" the symbol of the shares purchased (required)
    # param : "shares" the number of shares purchased (required)
    # param : "buyPrice" the price at which the shares were purchased (required)
    # param : "buyDate" the date the lot was purchased (required)
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument("id")
        parser.add_argument("password")
        parser.add_argument("symbol")
        parser.add_argument("shares")
        parser.add_argument("buyPrice")
        parser.add_argument("buyDate")
        params = parser.parse_args()

        if params["id"] is None:
            return Response(json.dumps({ "error" : MISSING_ID_PARAM, "message" : "'id' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["password"] is None:
            return Response(json.dumps({ "error" : MISSING_PASSWORD_PARAM, "message" : "'password' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkIntFormat(params["id"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not checkPasswordFormat(params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not matchIdAndPassword(params["id"], params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if params["symbol"] is None:
            return Response(json.dumps({ "error" : MISSING_SYMBOL_PARAM, "message" : "'symbol' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["shares"] is None:
            return Response(json.dumps({ "error" : MISSING_SHARES_PARAM, "message" : "'shares' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["buyPrice"] is None:
            return Response(json.dumps({ "error" : MISSING_BUY_PRICE_PARAM, "message" : "'buyPrice' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["buyDate"] is None:
            return Response(json.dumps({ "error" : MISSING_BUY_DATE_PARAM, "message" : "'buyDate' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkIntFormat(params["shares"]) or int(params["shares"]) <= 0:
            return Response(json.dumps({ "error" : INVALID_SHARES_PARAM, "message" : "Invalid amount of shares" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkDollarFormat(params["buyPrice"]):
            return Response(json.dumps({ "error" : INVALID_BUY_PRICE_PARAM, "message" : "Invalid buy price" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkDateFormat(params["buyDate"]):
            return Response(json.dumps({ "error" : INVALID_BUY_DATE_PARAM, "message" : "Invalid buy date" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not symbolExists(params["symbol"]):
            return Response(json.dumps({ "error" : INVALID_SYMBOL_PARAM, "message" : "Invalid symbol" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        lotId = createLotId()
        shareIds = createShareIds(int(params["shares"]))

        connection = getDbConnection()
        cursor = connection.cursor()
        try:
            for shareId in shareIds:
                cursor.execute(
                    "INSERT INTO Holdings (ShareID, LotID, User, Symbol, BuyPrice, BuyDate) VALUES (?, ?, ?, ?, ?, ?)",
                    (shareId, lotId, params["id"], params["symbol"].upper(), params["buyPrice"], params["buyDate"])
                )
            connection.commit()
        finally:
            connection.close()

        return Response(json.dumps({ "user" : int(params["id"]), "symbol" : params["symbol"].upper(), "shares" : int(params["shares"]), "buyPrice" : float(params["buyPrice"]), "buyDate" : params["buyDate"], "lotId" : lotId, "shareIds" : shareIds }), status=HTTP_OK, mimetype="application/json")


    # edit a lot's information
    #
    # param : "id" the user's id (required)
    # param : "password" the user's password (required)
    # param : "lotId" the lot to be edited (required)
    # param : "shares" the new number of shares purchased (*)
    # param : "buyPrice" the new price at which the shares were bought (*)
    # param : "buyDate" the new date at which the lot was purchased (*)
    #
    # (*) at least one of these parameters is required
    def patch(self):
        parser = reqparse.RequestParser()
        parser.add_argument("id")
        parser.add_argument("password")
        parser.add_argument("lotId")
        parser.add_argument("shares")
        parser.add_argument("buyPrice")
        parser.add_argument("buyDate")
        params = parser.parse_args()

        if params["id"] is None:
            return Response(json.dumps({ "error": MISSING_ID_PARAM, "message" : "'id' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["password"] is None:
            return Response(json.dumps({ "error": MISSING_PASSWORD_PARAM, "message" : "'password' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["lotId"] is None:
            return Response(json.dumps({ "error": MISSING_LOT_ID_PARAM, "message" : "'lotId' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["shares"] is None and params["buyPrice"] is None and params["buyDate"] is None:
            return Response(json.dumps({ "error": MISSING_REQUIRED_PARAMS, "message" : "'shares' and/or 'buyPrice' and/or 'buyDate' parameter(s) required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkIntFormat(params["id"]):
            return Response(json.dumps({ "error": AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not checkPasswordFormat(params["password"]):
            return Response(json.dumps({ "error": AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not matchIdAndPassword(params["id"], params["password"]):
            return Response(json.dumps({ "error": AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not lotOwnedByUser(params["lotId"], params["id"]):
            return Response(json.dumps({ "error": OWNERSHIP_AUTHENTICATION_FAILED, "message" : "User does not own this lot" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        infoChanges = []
        update_params = []

        if params["buyPrice"] is not None:
            if not checkDollarFormat(params["buyPrice"]):
                return Response(json.dumps({ "error" : INVALID_BUY_PRICE_PARAM, "message" : "Invalid buy price" }), status=HTTP_BAD_REQUEST, mimetype="application/json")
            infoChanges.append("BuyPrice = ?")
            update_params.append(params["buyPrice"])

        if params["buyDate"] is not None:
            if not checkDateFormat(params["buyDate"]):
                return Response(json.dumps({ "error" : INVALID_BUY_DATE_PARAM, "message" : "Invalid buy date" }), status=HTTP_BAD_REQUEST, mimetype="application/json")
            infoChanges.append("BuyDate = ?")
            update_params.append(params["buyDate"])

        if params["shares"] is not None:
            if not checkIntFormat(params["shares"]) or int(params["shares"]) <= 0:
                return Response(json.dumps({ "error" : INVALID_SHARES_PARAM, "message" : "Invalid amount of shares" }), status=HTTP_BAD_REQUEST, mimetype="application/json")
            query = "SELECT COUNT(*), User, Symbol, BuyPrice, BuyDate FROM Holdings WHERE LotID = ?;"
            result = executeDatabaseQuery(query, (params["lotId"],))
            currentShares = int(result[0][0])
            user = int(result[0][1])
            symbol = result[0][2]
            buyPrice = round(float(result[0][3]), 2)
            buyDate = result[0][4]

            # increase number of shares in this lot
            if currentShares < int(params["shares"]):
                shareIds = createShareIds(int(params["shares"]) - int(currentShares))

                connection = getDbConnection()
                cursor = connection.cursor()
                try:
                    for shareId in shareIds:
                        cursor.execute(
                            "INSERT INTO Holdings (ShareID, LotID, User, Symbol, BuyPrice, BuyDate) VALUES (?, ?, ?, ?, ?, ?)",
                            (shareId, params["lotId"], user, symbol, buyPrice, buyDate)
                        )
                    connection.commit()
                finally:
                    connection.close()

            # decrease number of shares in this lot
            elif currentShares > int(params["shares"]):
                remove = currentShares - int(params["shares"])
                query = "SELECT COUNT(*) FROM Holdings WHERE LotID = ? AND SellLotID IS NULL;"
                result = executeDatabaseQuery(query, (params["lotId"],))

                # if there are enough unsold shares left in the lot, remove them
                if int(result[0][0]) >= remove:
                    query = "SELECT ShareId FROM Holdings WHERE LotID = ? AND SellLotID IS NULL LIMIT ?;"
                    result = executeDatabaseQuery(query, (params["lotId"], remove))
                    share_ids_to_delete = [row[0] for row in result]

                    placeholders = ','.join('?' * len(share_ids_to_delete))
                    statement = f"DELETE FROM Holdings WHERE ShareId IN ({placeholders});"
                    executeDatabaseUpdate(statement, tuple(share_ids_to_delete))

                # otherwise check if there are enough shares of this symbol to replace shares removed from sell lots
                else:
                    removeFromSold = remove - int(result[0][0])
                    query = "SELECT COUNT(*) FROM Holdings WHERE User = ? AND Symbol = ? AND LotID <> ? AND SellLotID IS NULL;"
                    result = executeDatabaseQuery(query, (params["id"], symbol, params["lotId"]))
                    otherHoldings = int(result[0][0])

                    # if there aren't enough other shares of this symbol to replace the sold shares in this lot, throw an error
                    if removeFromSold > otherHoldings:
                        return Response(json.dumps({ "error" : BOUGHT_SOLD_DISCREPANCY, "message" : "Lowering the shares in this lot to {} would result in more shares being sold than bought".format(params["shares"]) }), status=HTTP_CONFLICT, mimetype="application/json")

                    # otherwise remove the appropriate amount of shares in this lot and replace the ones that were sold
                    query = "SELECT ShareID, SellLotID, SellPrice, SellDate FROM Holdings WHERE LotID = ? AND SellLotID IS NOT NULL LIMIT ?;"
                    result = executeDatabaseQuery(query, (params["lotId"], removeFromSold))
                    sharesToDelete = []
                    for row in result:
                        sharesToDelete.append(row[0])
                        query = "SELECT ShareID FROM Holdings WHERE User = ? AND Symbol = ? AND LotID <> ? AND SellLotID IS NULL ORDER BY BuyPrice ASC, LotID LIMIT 1;"
                        replacementShare = executeDatabaseQuery(query, (params["id"], symbol, params["lotId"]))
                        statement = "UPDATE Holdings SET SellLotID = ?, SellPrice = ?, SellDate = ? WHERE ShareId = ?;"
                        executeDatabaseUpdate(statement, (row[1], row[2], row[3], replacementShare[0][0]))

                    placeholders = ','.join('?' * len(sharesToDelete))
                    statement = f"DELETE FROM Holdings WHERE ShareID IN ({placeholders});"
                    executeDatabaseUpdate(statement, tuple(sharesToDelete))

                    statement = "DELETE FROM Holdings WHERE LotID = ? AND SellLotID IS NULL;"
                    executeDatabaseUpdate(statement, (params["lotId"],))

        if infoChanges:
            update_params.append(params["lotId"])
            statement = f"UPDATE Holdings SET {', '.join(infoChanges)} WHERE LotID = ?;"
            rows_affected = executeDatabaseUpdate(statement, tuple(update_params))
            if rows_affected == 0:
                return Response(json.dumps({ "message" : "Internal Server Error" }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")

        query = "SELECT ShareID FROM Holdings WHERE LotID = ?;"
        result = executeDatabaseQuery(query, (params["lotId"],))
        shareIds = [int(row[0]) for row in result]

        query = "SELECT Symbol, BuyPrice, BuyDate FROM Holdings WHERE LotID = ?;"
        result = executeDatabaseQuery(query, (params["lotId"],))
        return Response(json.dumps({ "user" : int(params["id"]), "symbol" : result[0][0], "shares" : len(shareIds), "buyPrice" : float(result[0][1]), "buyDate" : result[0][2], "lotId" : params["lotId"], "shareIds" : shareIds }), status=HTTP_OK, mimetype="application/json")


    # remove a lot and its data
    #
    # param : "id" the user's id (required)
    # param : "password" the user's password (required)
    # param : "lotId" the lot to be removed (required)
    def delete(self):
        parser = reqparse.RequestParser()
        parser.add_argument("id")
        parser.add_argument("password")
        parser.add_argument("lotId")
        params = parser.parse_args()

        if params["id"] is None:
            return Response(json.dumps({ "error" : MISSING_ID_PARAM, "message" : "'id' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["password"] is None:
            return Response(json.dumps({ "error" : MISSING_PASSWORD_PARAM, "message" : "'password' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["lotId"] is None:
            return Response(json.dumps({ "error" : MISSING_LOT_ID_PARAM, "message" : "'lotId' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkIntFormat(params["id"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not checkPasswordFormat(params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not matchIdAndPassword(params["id"], params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not lotOwnedByUser(params["lotId"], params["id"]):
            return Response(json.dumps({ "error" : OWNERSHIP_AUTHENTICATION_FAILED, "message" : "User does not own this lot" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        query = "SELECT COUNT(*), Symbol FROM Holdings WHERE LotID = ?;"
        result = executeDatabaseQuery(query, (params["lotId"],))
        totalShares = int(result[0][0])
        symbol = result[0][1]

        query = "SELECT COUNT(*), SellLotID, SellPrice, SellDate FROM Holdings WHERE LotID = ? AND SellLotID IS NOT NULL GROUP BY SellLotID;"
        sellLots = executeDatabaseQuery(query, (params["lotId"],))
        soldStocks = sum(int(row[0]) for row in sellLots)

        query = "SELECT COUNT(*) FROM Holdings WHERE User = ? AND Symbol = ? AND LotID <> ? AND SellLotID IS NULL;"
        result = executeDatabaseQuery(query, (params["id"], symbol, params["lotId"]))
        otherHoldings = int(result[0][0])
        if soldStocks > otherHoldings:
            return Response(json.dumps({ "error" : BOUGHT_SOLD_DISCREPANCY, "message" : "Deleting this lot would result in more shares being sold than bought" }), status=HTTP_CONFLICT, mimetype="application/json")

        for sellLot in sellLots:
            query = "SELECT ShareID FROM Holdings WHERE User = ? AND Symbol = ? AND LotID <> ? AND SellLotID IS NULL ORDER BY BuyPrice ASC, LotID LIMIT ?;"
            result = executeDatabaseQuery(query, (params["id"], symbol, params["lotId"], int(sellLot[0])))
            shares = [int(row[0]) for row in result]

            placeholders = ','.join('?' * len(shares))
            statement = f"UPDATE Holdings SET SellLotID = ?, SellPrice = ?, SellDate = ? WHERE ShareId IN ({placeholders});"
            executeDatabaseUpdate(statement, (sellLot[1], sellLot[2], sellLot[3]) + tuple(shares))

        statement = "DELETE FROM Holdings WHERE LotID = ?;"
        executeDatabaseUpdate(statement, (params["lotId"],))
        return Response(json.dumps({ "lotId" : int(params["lotId"]) }), status=HTTP_OK, mimetype="application/json")


# /myshare/user/sell-lots
class SellLots(Resource):

    # get a user's sell lots
    #
    # param : "id" the user's id (required)
    # param : "password" the user's password (required)
    # param : "sellLotId" a filter to return info of only a specific sell lot
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument("id")
        parser.add_argument("password")
        parser.add_argument("sellLotId")
        params = parser.parse_args()

        if params["id"] is None:
            return Response(json.dumps({ "error" : MISSING_ID_PARAM, "message" : "'id' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["password"] is None:
            return Response(json.dumps({ "error" : MISSING_PASSWORD_PARAM, "message" : "'password' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkIntFormat(params["id"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not checkPasswordFormat(params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not matchIdAndPassword(params["id"], params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        sellLotFilter = "IS NOT NULL"
        filter_params = [params["id"]]
        if params["sellLotId"] is not None:
            if not lotSoldByUser(params["sellLotId"], params["id"]):
                return Response(json.dumps({ "error" : OWNERSHIP_AUTHENTICATION_FAILED, "message" : "User has not sold this lot" }), status=HTTP_FORBIDDEN, mimetype="application/json")
            sellLotFilter = "= ?"
            filter_params = [params["id"], params["sellLotId"]]

        sellLots = []
        symbol = None
        sellLot = None
        shares = None
        sellLotSharesCount = None
        buyPrice = None
        buyDate = None
        sellPrice = None
        sellDate = None
        lot = None
        lots = None
        totalProfit = 0.0
        totalWithdrawn = 0.0
        sellLotProfit = 0.0

        query = f"SELECT Symbol, SellLotID, ShareID, BuyPrice, BuyDate, SellPrice, SellDate, LotID FROM Holdings WHERE User = ? AND SellLotID {sellLotFilter} ORDER BY SellDate DESC, SellLotID, BuyDate, LotID;"
        result = executeDatabaseQuery(query, tuple(filter_params))

        for row in result:
            # for the first returned row, save the row's information
            if symbol is None:
                symbol = row[0]
                sellLot = int(row[1])
                buyPrice = round(float(row[3]), 2)
                buyDate = row[4]
                sellPrice = round(float(row[5]), 2)
                sellDate = row[6]
                lot = { "lotId" : int(row[7]), "buyPrice": buyPrice, "buyDate": buyDate }
                lots = []
                shares = [int(row[2])]
                sellLotSharesCount = 1

            # if the sell lot is different than the current sell lot,
            # add the current sell lot to sell lots then then save this row's information
            elif sellLot != row[1]:
                lotProfit = len(shares) * (sellPrice - buyPrice)
                sellLotProfit += lotProfit
                totalProfit += sellLotProfit
                totalWithdrawn += sellLotSharesCount * sellPrice
                lot.update({ "profit" : round(lotProfit, 2), "shares" : len(shares), "shareIds" : shares })
                lots.append(lot)
                sellLots.append({ "sellLotId" : sellLot, "symbol" : symbol,  "sellPrice" : sellPrice, "sellDate" : sellDate, "profit" : round(sellLotProfit, 2), "sharesSold" : sellLotSharesCount, "lots" : lots })
                symbol = row[0]
                sellLot = int(row[1])
                buyPrice = round(float(row[3]), 2)
                buyDate = row[4]
                sellPrice = round(float(row[5]), 2)
                sellDate = row[6]
                lot = { "lotId" : int(row[7]), "buyPrice": buyPrice, "buyDate": buyDate }
                lots = []
                shares = [int(row[2])]
                sellLotSharesCount = 1

            # if the lot is different than the current lot,
            # add the current lot to lots then then save this row's information
            elif lot["lotId"] != row[7]:
                lotProfit = len(shares) * (sellPrice - buyPrice)
                sellLotProfit += lotProfit
                lot.update({ "profit" : round(lotProfit, 2), "shares" : len(shares), "shareIds" : shares })
                lots.append(lot)
                buyPrice = round(float(row[3]), 2)
                buyDate = row[4]
                lot = { "lotId" : int(row[7]), "buyPrice": buyPrice, "buyDate": buyDate }
                shares = [int(row[2])]
                sellLotSharesCount += 1

            # otherwise, add this share to shares
            else:
                shares.append(int(row[2]))
                sellLotSharesCount += 1

        if symbol is not None:
            lotProfit = len(shares) * (sellPrice - buyPrice)
            sellLotProfit += lotProfit
            totalProfit += sellLotProfit
            totalWithdrawn += sellLotSharesCount * sellPrice
            lot.update({ "profit" : round(lotProfit, 2), "shares" : len(shares), "shareIds" : shares })
            lots.append(lot)
            sellLots.append({ "sellLotId" : sellLot, "symbol" : symbol,  "sellPrice" : sellPrice, "sellDate" : sellDate, "profit" : round(sellLotProfit, 2), "sharesSold" : sellLotSharesCount, "lots" : lots })

        if params["sellLotId"] is not None:
            return Response(json.dumps(sellLots[0]), status=HTTP_OK, mimetype="application/json")
        return Response(json.dumps({ "id" : int(params["id"]), "totalWithdrawn" : round(totalWithdrawn, 2), "totalProfit" : round(totalProfit, 2), "sellLots" : sellLots }), status=HTTP_OK, mimetype="application/json")


    # add a new sell lot for a user
    #
    # param : "id" the user's id (required)
    # param : "password" the user's password (required)
    # param : "symbol" the symbol of the shares sold (*)
    # param : "shares" the number of shares sold (*)
    # param : "shareId" the date the lot was sold (**)
    # param : "sellPrice" the price at which the shares were sold (required)
    # param : "sellDate" the date the lot was sold (required)
    #
    # Either all params marked (*) or all params marked (**) required
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument("id")
        parser.add_argument("password")
        parser.add_argument("symbol")
        parser.add_argument("shares")
        parser.add_argument("shareId")
        parser.add_argument("sellPrice")
        parser.add_argument("sellDate")
        params = parser.parse_args()

        if params["id"] is None:
            return Response(json.dumps({ "error" : MISSING_ID_PARAM, "message" : "'id' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["password"] is None:
            return Response(json.dumps({ "error" : MISSING_PASSWORD_PARAM, "message" : "'password' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["sellPrice"] is None:
            return Response(json.dumps({ "error" : MISSING_SELL_PRICE_PARAM, "message" : "'sellPrice' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["sellDate"] is None:
            return Response(json.dumps({ "error" : MISSING_SELL_DATE_PARAM, "message" : "'sellDate' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if (params["symbol"] is None or params["shares"] is None) and params["shareId"] is None:
            return Response(json.dumps({ "error" : MISSING_REQUIRED_PARAMS, "message" : "'symbol' and 'shares' or 'shareId' parameter(s) required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkDollarFormat(params["sellPrice"]):
            return Response(json.dumps({ "error" : INVALID_SELL_PRICE_PARAM, "message" : "Invalid sell price" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkDateFormat(params["sellDate"]):
            return Response(json.dumps({ "error" : INVALID_SELL_DATE_PARAM, "message" : "Invalid sell date" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkIntFormat(params["id"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not checkPasswordFormat(params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not matchIdAndPassword(params["id"], params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if params["shareId"] is not None:
            if not shareHeldByUser(params["shareId"], params["id"]):
                return Response(json.dumps({ "error" : OWNERSHIP_AUTHENTICATION_FAILED, "message" : "User is not holding this share" }), status=HTTP_FORBIDDEN, mimetype="application/json")
            sellLotId = createSellLotId()
            statement = "UPDATE Holdings SET SellLotID = ?, SellPrice = ?, SellDate = ? WHERE ShareID = ?;"
            sell = executeDatabaseUpdate(statement, (sellLotId, params["sellPrice"], params["sellDate"], params["shareId"]))
            if sell == 0:
                return Response(json.dumps({ "message" : "Internal Server Error" }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")
            return Response(json.dumps({ "sellLotId" : sellLotId, "shareId" : int(params["shareId"]) }), status=HTTP_OK, mimetype="application/json")

        if not symbolExists(params["symbol"]):
            return Response(json.dumps({ "error" : INVALID_SYMBOL_PARAM, "message" : "Invalid symbol" }), status=HTTP_BAD_REQUEST, mimetype="application/json")
        if not checkIntFormat(params["shares"]) or int(params["shares"]) <= 0:
            return Response(json.dumps({ "error" : INVALID_SHARES_PARAM, "message" : "Invalid amount of shares" }), status=HTTP_BAD_REQUEST, mimetype="application/json")
        query = "SELECT COUNT(*) FROM Holdings WHERE User = ? AND Symbol = ? AND SellLotID IS NULL;"
        result = executeDatabaseQuery(query, (params["id"], params["symbol"].upper()))
        if result[0][0] < int(params["shares"]):
            return Response(json.dumps({ "error" : BOUGHT_SOLD_DISCREPANCY, "message" : "Selling {} shares would result in more shares being sold than bought".format(params["shares"]) }), status=HTTP_CONFLICT, mimetype="application/json")
        sellLotId = createSellLotId()

        query = "SELECT ShareID, BuyPrice FROM Holdings WHERE User = ? AND Symbol = ? AND SellLotID IS NULL ORDER BY BuyPrice ASC, LotID LIMIT ?;"
        result = executeDatabaseQuery(query, (params["id"], params["symbol"].upper(), int(params["shares"])))
        shares = []
        profit = 0.0
        for row in result:
            shares.append(int(row[0]))
            profit += float(params["sellPrice"]) - float(row[1])

        placeholders = ','.join('?' * len(shares))
        statement = f"UPDATE Holdings SET SellLotID = ?, SellPrice = ?, SellDate = ? WHERE ShareID IN ({placeholders});"
        update = executeDatabaseUpdate(statement, (sellLotId, params["sellPrice"], params["sellDate"]) + tuple(shares))
        if update == 0:
            return Response(json.dumps({ "message" : "Internal Server Error" }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")
        return Response(json.dumps({ "sellLotId" : sellLotId, "profit" : round(profit, 2), "shares" : len(shares), "shareIds" : shares }), status=HTTP_OK, mimetype="application/json")


    # edit a sell lot's information
    #
    # param : "id" the user's id (required)
    # param : "password" the user's password (required)
    # param : "sellLotId" the sell lot to be edited (required)
    # param : "shares" the new number of shares sold (*)
    # param : "sellPrice" the new price at which the shares were sold (*)
    # param : "sellDate" the new date at which the lot was sold (*)
    #
    # (*) at least one of these parameters is required
    def patch(self):
        parser = reqparse.RequestParser()
        parser.add_argument("id")
        parser.add_argument("password")
        parser.add_argument("sellLotId")
        parser.add_argument("shares")
        parser.add_argument("sellPrice")
        parser.add_argument("sellDate")
        params = parser.parse_args()

        if params["id"] is None:
            return Response(json.dumps({ "error": MISSING_ID_PARAM, "message" : "'id' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["password"] is None:
            return Response(json.dumps({ "error": MISSING_PASSWORD_PARAM, "message" : "'password' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["sellLotId"] is None:
            return Response(json.dumps({ "error": MISSING_SELL_LOT_ID_PARAM, "message" : "'sellLotId' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["shares"] is None and params["sellPrice"] is None and params["sellDate"] is None:
            return Response(json.dumps({ "error": MISSING_REQUIRED_PARAMS, "message" : "'shares' and/or 'sellPrice' and/or 'sellDate' parameter(s) required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkIntFormat(params["id"]):
            return Response(json.dumps({ "error": AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not checkPasswordFormat(params["password"]):
            return Response(json.dumps({ "error": AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not matchIdAndPassword(params["id"], params["password"]):
            return Response(json.dumps({ "error": AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not lotSoldByUser(params["sellLotId"], params["id"]):
            return Response(json.dumps({ "error": OWNERSHIP_AUTHENTICATION_FAILED, "message" : "User has not sold this lot" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        infoChanges = []
        update_params = []

        if params["sellPrice"] is not None:
            if not checkDollarFormat(params["sellPrice"]):
                return Response(json.dumps({ "error": INVALID_SELL_PRICE_PARAM, "message" : "Invalid sell price" }), status=HTTP_BAD_REQUEST, mimetype="application/json")
            infoChanges.append("SellPrice = ?")
            update_params.append(params["sellPrice"])

        if params["sellDate"] is not None:
            if not checkDateFormat(params["sellDate"]):
                return Response(json.dumps({ "error": INVALID_SELL_DATE_PARAM, "message" : "Invalid sell date" }), status=HTTP_BAD_REQUEST, mimetype="application/json")
            infoChanges.append("SellDate = ?")
            update_params.append(params["sellDate"])

        if params["shares"] is not None:
            if not checkIntFormat(params["shares"]) or int(params["shares"]) <= 0:
                return Response(json.dumps({ "error": INVALID_SHARES_PARAM, "message" : "Invalid amount of shares" }), status=HTTP_BAD_REQUEST, mimetype="application/json")
            query = "SELECT COUNT(*), Symbol, SellPrice, SellDate FROM Holdings WHERE SellLotID = ?;"
            result = executeDatabaseQuery(query, (params["sellLotId"],))
            currentShares = int(result[0][0])
            symbol = result[0][1]
            sellPrice = round(float(result[0][2]), 2)
            sellDate = result[0][3]

            # decrease number of shares sold in this lot
            if currentShares > int(params["shares"]):
                remove = int(currentShares) - int(params["shares"])

                query = "SELECT ShareID FROM Holdings WHERE SellLotID = ? ORDER BY BuyPrice DESC LIMIT ?;"
                result = executeDatabaseQuery(query, (params["sellLotId"], remove))
                shares = [int(row[0]) for row in result]

                placeholders = ','.join('?' * len(shares))
                statement = f"UPDATE Holdings SET SellLotID = NULL, SellPrice = NULL, SellDate = NULL WHERE ShareID IN ({placeholders});"
                update = executeDatabaseUpdate(statement, tuple(shares))
                if update == 0:
                    return Response(json.dumps({ "message" : "Internal Server error" }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")

            # increase number of shares sold in this lot
            elif currentShares < int(params["shares"]):
                add = int(params["shares"]) - int(currentShares)
                query = "SELECT COUNT(*) FROM Holdings WHERE User = ? AND Symbol = ? AND SellLotID IS NULL;"
                result = executeDatabaseQuery(query, (params["id"], symbol))
                otherHoldings = int(result[0][0])

                # if there aren't enough shares of this symbol in holdings to accommodate this change, throw an error
                if otherHoldings < add:
                    return Response(json.dumps({ "error": BOUGHT_SOLD_DISCREPANCY, "message" : "Increasing the shares sold in this lot to {} would result in more shares being sold than bought".format(params["shares"]) }), status=HTTP_CONFLICT, mimetype="application/json")

                query = "SELECT ShareID FROM Holdings WHERE User = ? AND Symbol = ? AND SellLotID IS NULL ORDER BY BuyPrice ASC, LotID LIMIT ?;"
                result = executeDatabaseQuery(query, (params["id"], symbol, add))
                shares = [int(row[0]) for row in result]

                placeholders = ','.join('?' * len(shares))
                statement = f"UPDATE Holdings SET SellLotID = ?, SellPrice = ?, SellDate = ? WHERE ShareID IN ({placeholders});"
                update = executeDatabaseUpdate(statement, (params["sellLotId"], sellPrice, sellDate) + tuple(shares))
                if update == 0:
                    return Response(json.dumps({ "message" : "Internal Server Error" }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")

        if infoChanges:
            update_params.append(params["sellLotId"])
            statement = f"UPDATE Holdings SET {', '.join(infoChanges)} WHERE SellLotID = ?;"
            update = executeDatabaseUpdate(statement, tuple(update_params))
            if update == 0:
                return Response(json.dumps({ "message" : "Internal Server Error" }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")

        query = "SELECT ShareID FROM Holdings WHERE SellLotID = ?;"
        result = executeDatabaseQuery(query, (params["sellLotId"],))
        shareIds = [int(row[0]) for row in result]

        query = "SELECT Symbol, SellPrice, SellDate FROM Holdings WHERE SellLotID = ?;"
        result = executeDatabaseQuery(query, (params["sellLotId"],))
        return Response(json.dumps({ "user" : int(params["id"]), "symbol" : result[0][0], "shares" : len(shareIds), "sellPrice" : float(result[0][1]), "sellDate" : result[0][2], "sellLotId" : params["sellLotId"], "shareIds" : shareIds }), status=HTTP_OK, mimetype="application/json")


    # remove a sell lot and its data
    #
    # param : "id" the user's id (required)
    # param : "password" the user's password (required)
    # param : "sellLotId" the sell lot to be removed (required)
    def delete(self):
        parser = reqparse.RequestParser()
        parser.add_argument("id")
        parser.add_argument("password")
        parser.add_argument("sellLotId")
        params = parser.parse_args()

        if params["id"] is None:
            return Response(json.dumps({ "error" : MISSING_ID_PARAM, "message" : "'id' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["password"] is None:
            return Response(json.dumps({ "error" : MISSING_PASSWORD_PARAM, "message" : "'password' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["sellLotId"] is None:
            return Response(json.dumps({ "error" : MISSING_SELL_LOT_ID_PARAM, "message" : "'sellLotId' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkIntFormat(params["id"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not checkPasswordFormat(params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not matchIdAndPassword(params["id"], params["password"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and password do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if not lotSoldByUser(params["sellLotId"], params["id"]):
            return Response(json.dumps({ "error" : OWNERSHIP_AUTHENTICATION_FAILED, "message" : "User has not sold this lot" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        statement = "UPDATE Holdings SET SellLotID = NULL, SellPrice = NULL, SellDate = NULL WHERE SellLotID = ?;"
        update = executeDatabaseUpdate(statement, (params["sellLotId"],))
        if update == 0:
            return Response(json.dumps({ "message" : "Internal Server Error" }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")
        return Response(json.dumps({ "sellLotId" : int(params["sellLotId"]) }), status=HTTP_OK, mimetype="application/json")


# /myshare/user/password-reset/validate
class ValidateResetCode(Resource):
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument("username")
        parser.add_argument("resetCode")
        params = parser.parse_args()

        if params["username"] is None:
            return Response(json.dumps({ "error" : MISSING_USERNAME_PARAM, "message" : "'username' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["resetCode"] is None:
            return Response(json.dumps({ "error" : MISSING_RESET_CODE_PARAM, "message" : "'resetCode' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkUsernameFormat(params["username"]) or usernameAvailable(params["username"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Username and reset code do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        statement = "DELETE FROM PasswordReset WHERE (Cast((JulianDay('now', 'localtime') - JulianDay(Created, 'localtime')) * 24 * 60 AS INTEGER)) > 5;"
        executeDatabaseUpdate(statement)

        query = "SELECT ID FROM Users WHERE Username = ?;"
        result = executeDatabaseQuery(query, (params["username"],))
        id = result[0][0]

        query = "SELECT ResetCode, Attempts FROM PasswordReset WHERE User = ?;"
        result = executeDatabaseQuery(query, (id,))
        if result == []:
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Username and reset code do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if int(result[0][1]) >= 5:
            return Response(json.dumps({ "error" : TOO_MANY_AUTHENTICATION_ATTEMPTS, "message" : "Too many attempts" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if result[0][0] == params["resetCode"].upper():
            return Response(json.dumps({ "id" : id }), status=HTTP_OK, mimetype="application/json")

        statement = "UPDATE PasswordReset SET Attempts = Attempts + 1 WHERE User = ?;"
        executeDatabaseUpdate(statement, (id,))
        return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Username and reset code do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")


# /myshare/user/password-reset
class PasswordReset(Resource):

    # email a user a password reset code
    #
    # param : "email" the user's email (required)
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument("email")
        params = parser.parse_args()

        if params["email"] is None:
            return Response(json.dumps({ "error" : MISSING_EMAIL_PARAM, "message" : "'email' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkEmailFormat(params["email"]):
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Email not registered to any user" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        query = "SELECT ID, FirstName, Username FROM Users WHERE Email = ?;"
        result = executeDatabaseQuery(query, (params["email"],))
        if result == []:
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Email not registered to any user" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        resetCode = str(secrets.token_hex(4)).upper()

        update = "INSERT INTO PasswordReset (User, ResetCode) VALUES (?, ?);"
        insert = executeDatabaseUpdate(update, (result[0][0], resetCode))
        if insert == 0:
            return Response(json.dumps({ "message" : "Internal Server Error" }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")

        # Check if email credentials are configured
        if not ADMIN_EMAIL_ADDRESS or not ADMIN_EMAIL_PASSWORD:
            return Response(json.dumps({ "message" : "Email service not configured. Set ADMIN_EMAIL_ADDRESS and ADMIN_EMAIL_PASSWORD environment variables." }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")

        yag_smtp_connection = yagmail.SMTP(user=ADMIN_EMAIL_ADDRESS, password=ADMIN_EMAIL_PASSWORD, host='smtp.gmail.com')
        subject = "FinSTR Password Recovery"
        contents = [createPasswordResetEmail(result[0][1], result[0][2], resetCode)]
        try:
            yag_smtp_connection.send(params["email"], subject, contents)
        except:
            return Response(json.dumps({ "message" : "Internal Server Error" }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")
        return Response(json.dumps({ "email" : params["email"] }), status=HTTP_OK, mimetype="application/json")


    # reset a users password using a code sent to their email
    #
    # param : "id" the user's id (required)
    # param : "resetCode" the reset code sent to the user's email (required)
    # param : "newPassword" the user's new password (required)
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument("id")
        parser.add_argument("resetCode")
        parser.add_argument("newPassword")
        params = parser.parse_args()

        if params["id"] is None:
            return Response(json.dumps({ "error" : MISSING_ID_PARAM, "message" : "'id' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["resetCode"] is None:
            return Response(json.dumps({ "error" : MISSING_RESET_CODE_PARAM, "message" : "'resetCode' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if params["newPassword"] is None:
            return Response(json.dumps({ "error" : MISSING_NEW_PASSWORD_PARAM, "message" : "'newPassword' parameter required" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        if not checkPasswordFormat(params["newPassword"]):
            return Response(json.dumps({ "error" : INVALID_NEW_PASSWORD_PARAM, "message" : "Invalid new password" }), status=HTTP_BAD_REQUEST, mimetype="application/json")

        statement = "DELETE FROM PasswordReset WHERE (Cast((JulianDay('now', 'localtime') - JulianDay(Created, 'localtime')) * 24 * 60 AS INTEGER)) > 5;"
        executeDatabaseUpdate(statement)

        query = "SELECT ResetCode, Attempts FROM PasswordReset WHERE User = ?;"
        result = executeDatabaseQuery(query, (params["id"],))
        if result == []:
            return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and reset code do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if int(result[0][1]) >= 5:
            return Response(json.dumps({ "error" : TOO_MANY_AUTHENTICATION_ATTEMPTS, "message" : "Too many attempts" }), status=HTTP_FORBIDDEN, mimetype="application/json")

        if result[0][0] == params["resetCode"].upper():
            statement = "DELETE FROM PasswordReset WHERE User = ?;"
            executeDatabaseUpdate(statement, (params["id"],))
            password = sha256_crypt.encrypt(params["newPassword"])
            statement = "UPDATE Users SET Password = ? WHERE ID = ?;"
            update = executeDatabaseUpdate(statement, (password, params["id"]))
            if update == 0:
                return Response(json.dumps({ "message" : "Internal Server Error" }), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")
            return Response(json.dumps({ "id" : params["id"] }), status=HTTP_OK, mimetype="application/json")

        statement = "UPDATE PasswordReset SET Attempts = Attempts + 1 WHERE User = ?;"
        executeDatabaseUpdate(statement, (params["id"],))
        return Response(json.dumps({ "error" : AUTHENTICATION_FAILED, "message" : "Id and reset code do not match" }), status=HTTP_FORBIDDEN, mimetype="application/json")

# /myshare/symbol
class SymbolPage(Resource):
    def get(self):
        return Response(render_template("symbol.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/home
class HomePage(Resource):
    def get(self):
        return Response(render_template("home.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/login
class LoginPage(Resource):
    def get(self):
        return Response(render_template("login.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/register
class RegisterPage(Resource):
    def get(self):
        return Response(render_template("register.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/recover-password
class RecoverPasswordPage(Resource):
    def get(self):
        return Response(render_template("recover-password.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/reset-password
class ResetPasswordPage(Resource):
    def get(self):
        return Response(render_template("reset-password.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/account-settings
class AccountSettingsPage(Resource):
    def get(self):
        return Response(render_template("account-settings.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/change-password
class ChangePasswordPage(Resource):
    def get(self):
        return Response(render_template("change-password.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/delete-account
class DeleteAccountPage(Resource):
    def get(self):
        return Response(render_template("delete-account.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/holdings
class HoldingsPage(Resource):
    def get(self):
        return Response(render_template("holdings.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/lots
class LotsPage(Resource):
    def get(self):
        return Response(render_template("lots.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/sell-lots
class SellLotsPage(Resource):
    def get(self):
        return Response(render_template("sell-lots.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/lot
class LotPage(Resource):
    def get(self):
        return Response(render_template("lot.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/sell-lot
class SellLotPage(Resource):
    def get(self):
        return Response(render_template("sell-lot.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/new-lot
class NewLotPage(Resource):
    def get(self):
        return Response(render_template("new-lot.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/new-sell-lot
class NewSellLotPage(Resource):
    def get(self):
        return Response(render_template("new-sell-lot.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/edit-lot
class EditLotPage(Resource):
    def get(self):
        return Response(render_template("edit-lot.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/edit-sell-lot
class EditSellLotPage(Resource):
    def get(self):
        return Response(render_template("edit-sell-lot.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/delete-lot
class DeleteLotPage(Resource):
    def get(self):
        return Response(render_template("delete-lot.html"), status=HTTP_OK, mimetype="text/html")

# /myshare/edit-sell-lot
class DeleteSellLotPage(Resource):
    def get(self):
        return Response(render_template("delete-sell-lot.html"), status=HTTP_OK, mimetype="text/html")



# /api/chart
class ChartData(Resource):
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument("ticker")
        parser.add_argument("period", default="1y")
        parser.add_argument("interval", default="1d")
        params = parser.parse_args()
        if not params["ticker"]:
            return Response(json.dumps({"error": "ticker required"}), status=HTTP_BAD_REQUEST, mimetype="application/json")
        try:
            hist = yfinance.Ticker(params["ticker"]).history(period=params["period"], interval=params["interval"])
            rows = []
            for dt, row in hist.iterrows():
                rows.append({
                    "time": dt.strftime("%Y-%m-%d"),
                    "open": round(float(row["Open"]), 2),
                    "high": round(float(row["High"]), 2),
                    "low": round(float(row["Low"]), 2),
                    "close": round(float(row["Close"]), 2),
                    "volume": int(row["Volume"])
                })
            resp = Response(json.dumps(rows), status=HTTP_OK, mimetype="application/json")
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
        except Exception as e:
            return Response(json.dumps({"error": str(e)}), status=HTTP_BAD_REQUEST, mimetype="application/json")


# /api/news
import urllib.request
import xml.etree.ElementTree as ET

class MarketNews(Resource):
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument("ticker")
        params = parser.parse_args()
        articles = []
        try:
            if params["ticker"]:
                news = yfinance.Ticker(params["ticker"]).news or []
                for a in news[:25]:
                    thumb = ""
                    if a.get("thumbnail"):
                        res = a["thumbnail"].get("resolutions", [])
                        thumb = res[0].get("url", "") if res else ""
                    articles.append({
                        "title": a.get("title", ""),
                        "link": a.get("link", ""),
                        "publisher": a.get("publisher", ""),
                        "publishedAt": a.get("providerPublishTime", 0),
                        "description": "",
                        "thumbnail": thumb
                    })
            else:
                # General market news via Yahoo Finance RSS (free, no key)
                rss_url = "https://feeds.finance.yahoo.com/rss/2.0/headline?region=US&lang=en-US"
                try:
                    with urllib.request.urlopen(rss_url, timeout=6) as r:
                        tree = ET.parse(r)
                    for item in tree.findall(".//item")[:30]:
                        articles.append({
                            "title": item.findtext("title", ""),
                            "link": item.findtext("link", ""),
                            "publisher": "Yahoo Finance",
                            "publishedAt": item.findtext("pubDate", ""),
                            "description": (item.findtext("description", "") or "")[:250],
                            "thumbnail": ""
                        })
                except Exception:
                    # Fallback: pull news from key market tickers
                    seen = set()
                    for tk in ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]:
                        for a in (yfinance.Ticker(tk).news or []):
                            if a.get("title") not in seen:
                                seen.add(a.get("title"))
                                articles.append({
                                    "title": a.get("title", ""),
                                    "link": a.get("link", ""),
                                    "publisher": a.get("publisher", ""),
                                    "publishedAt": a.get("providerPublishTime", 0),
                                    "description": "",
                                    "thumbnail": ""
                                })
                    articles.sort(key=lambda x: x.get("publishedAt", 0) if isinstance(x.get("publishedAt"), int) else 0, reverse=True)
                    articles = articles[:30]
        except Exception as e:
            return Response(json.dumps({"error": str(e)}), status=HTTP_INTERNAL_SERVER_ERROR, mimetype="application/json")
        resp = Response(json.dumps(articles), status=HTTP_OK, mimetype="application/json")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp


# /api/futures
FUTURES_TICKERS = {
    "S&P 500 Fut.": "ES=F", "Nasdaq 100 Fut.": "NQ=F", "Dow Jones Fut.": "YM=F",
    "Russell 2000 Fut.": "RTY=F", "Gold": "GC=F", "Silver": "SI=F",
    "Crude Oil (WTI)": "CL=F", "Natural Gas": "NG=F",
    "Wheat": "ZW=F", "Corn": "ZC=F", "10Y T-Note": "ZN=F", "30Y T-Bond": "ZB=F"
}

class FuturesData(Resource):
    def get(self):
        results = []
        for name, symbol in FUTURES_TICKERS.items():
            try:
                info = yfinance.Ticker(symbol).info
                price = float(info.get("regularMarketPrice") or info.get("currentPrice") or 0)
                prev = float(info.get("previousClose") or price)
                chg = round(price - prev, 3)
                chg_pct = round((chg / prev * 100), 2) if prev else 0
                results.append({
                    "name": name, "symbol": symbol,
                    "price": round(price, 3), "change": chg, "changePercent": chg_pct,
                    "dayHigh": round(float(info.get("dayHigh") or 0), 3),
                    "dayLow": round(float(info.get("dayLow") or 0), 3),
                    "volume": int(info.get("volume") or 0)
                })
            except Exception:
                pass
        resp = Response(json.dumps(results), status=HTTP_OK, mimetype="application/json")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp


# /api/forex
FOREX_PAIRS = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
    "USD/CAD": "USDCAD=X", "AUD/USD": "AUDUSD=X", "USD/CHF": "USDCHF=X",
    "NZD/USD": "NZDUSD=X", "EUR/GBP": "EURGBP=X", "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X", "USD/CNY": "USDCNY=X", "USD/MXN": "USDMXN=X"
}

class ForexData(Resource):
    def get(self):
        results = []
        for pair, symbol in FOREX_PAIRS.items():
            try:
                info = yfinance.Ticker(symbol).info
                rate = float(info.get("regularMarketPrice") or info.get("currentPrice") or 0)
                prev = float(info.get("previousClose") or rate)
                chg = round(rate - prev, 5)
                chg_pct = round((chg / prev * 100), 3) if prev else 0
                results.append({
                    "name": pair, "symbol": symbol,
                    "price": round(rate, 5), "change": chg, "changePercent": chg_pct,
                    "dayHigh": round(float(info.get("dayHigh") or 0), 5),
                    "dayLow": round(float(info.get("dayLow") or 0), 5),
                    "bid": round(float(info.get("bid") or 0), 5),
                    "ask": round(float(info.get("ask") or 0), 5),
                })
            except Exception:
                pass
        resp = Response(json.dumps(results), status=HTTP_OK, mimetype="application/json")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp


# /api/prices — bulk price snapshot, 2-minute in-memory cache
_price_cache = {"data": None, "ts": 0}
PRICE_CACHE_TTL = 120  # seconds

class PriceSnapshot(Resource):
    def get(self):
        now = time.time()
        if _price_cache["data"] and now - _price_cache["ts"] < PRICE_CACHE_TTL:
            resp = Response(json.dumps(_price_cache["data"]), status=HTTP_OK, mimetype="application/json")
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
        try:
            raw = yfinance.download(
                " ".join(SCREENER_TICKERS),
                period="2d", interval="1d", progress=False, auto_adjust=True
            )
            closes = raw["Close"]
            results = []
            for ticker in SCREENER_TICKERS:
                try:
                    col = ticker if ticker in closes.columns else None
                    if col is None:
                        continue
                    vals = closes[col].dropna()
                    if len(vals) >= 2:
                        price = float(vals.iloc[-1])
                        prev  = float(vals.iloc[-2])
                    elif len(vals) == 1:
                        price = float(vals.iloc[-1])
                        prev  = price
                    else:
                        continue
                    chg = round(price - prev, 4)
                    chg_pct = round(chg / prev * 100, 2) if prev else 0
                    results.append({
                        "ticker": ticker,
                        "price": round(price, 2),
                        "change": chg,
                        "changePercent": chg_pct,
                    })
                except Exception:
                    pass
            _price_cache["data"] = results
            _price_cache["ts"] = now
            resp = Response(json.dumps(results), status=HTTP_OK, mimetype="application/json")
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
        except Exception as e:
            if _price_cache["data"]:
                resp = Response(json.dumps(_price_cache["data"]), status=HTTP_OK, mimetype="application/json")
                resp.headers["Access-Control-Allow-Origin"] = "*"
                return resp
            return Response(json.dumps([]), status=HTTP_OK, mimetype="application/json")


# /api/screener
class Screener(Resource):
    def get(self):
        results = []
        to_fetch = []
        for ticker in SCREENER_TICKERS:
            cached = get_screener_cache(ticker)
            if cached:
                results.append(cached)
            else:
                to_fetch.append(ticker)
        if to_fetch:
            with ThreadPoolExecutor(max_workers=8) as ex:
                futures = {ex.submit(fetch_ticker_data, t): t for t in to_fetch}
                for future in as_completed(futures):
                    data = future.result()
                    if data:
                        results.append(data)
        results.sort(key=lambda x: x.get("marketCap", 0), reverse=True)
        resp = Response(json.dumps(results), status=HTTP_OK, mimetype="application/json")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp


# /api/price
class LivePrice(Resource):
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument("ticker")
        params = parser.parse_args()
        if not params["ticker"]:
            return Response(json.dumps({"error": "ticker required"}), status=HTTP_BAD_REQUEST, mimetype="application/json")
        try:
            price = round(stock_info.get_live_price(params["ticker"]), 2)
            return Response(json.dumps({"price": price}), status=HTTP_OK, mimetype="application/json")
        except Exception:
            return Response(json.dumps({"error": "Invalid ticker"}), status=HTTP_BAD_REQUEST, mimetype="application/json")


app = Flask(__name__)
myShare = Api(app)

@app.route('/')
@app.route('/screener')
def screener_index():
    return send_from_directory('.', 'index.html')

# API endpoints
myShare.add_resource(Info, "/myshare/info")
myShare.add_resource(Price, "/myshare/info/price")
myShare.add_resource(User, "/myshare/user")
myShare.add_resource(ID, "/myshare/user/id")
myShare.add_resource(Holdings, "/myshare/user/holdings")
myShare.add_resource(Lots, "/myshare/user/lots")
myShare.add_resource(SellLots, "/myshare/user/sell-lots")
myShare.add_resource(ValidateResetCode, "/myshare/user/password-reset/validate")
myShare.add_resource(PasswordReset, "/myshare/user/password-reset")

# pages
myShare.add_resource(SymbolPage, "/myshare/symbol")
myShare.add_resource(HomePage, "/myshare/home")
myShare.add_resource(LoginPage, "/myshare/login")
myShare.add_resource(RegisterPage, "/myshare/register")
myShare.add_resource(RecoverPasswordPage, "/myshare/recover-password")
myShare.add_resource(ResetPasswordPage, "/myshare/reset-password")
myShare.add_resource(AccountSettingsPage, "/myshare/account-settings")
myShare.add_resource(ChangePasswordPage, "/myshare/change-password")
myShare.add_resource(DeleteAccountPage, "/myshare/delete-account")
myShare.add_resource(HoldingsPage, "/myshare/holdings")
myShare.add_resource(LotsPage, "/myshare/lots")
myShare.add_resource(SellLotsPage, "/myshare/sell-lots")
myShare.add_resource(LotPage, "/myshare/lot")
myShare.add_resource(SellLotPage, "/myshare/sell-lot")
myShare.add_resource(NewLotPage, "/myshare/new-lot")
myShare.add_resource(NewSellLotPage, "/myshare/new-sell-lot")
myShare.add_resource(EditLotPage, "/myshare/edit-lot")
myShare.add_resource(EditSellLotPage, "/myshare/edit-sell-lot")
myShare.add_resource(DeleteLotPage, "/myshare/delete-lot")
myShare.add_resource(DeleteSellLotPage, "/myshare/delete-sell-lot")

myShare.add_resource(PriceSnapshot, "/api/prices")
myShare.add_resource(Screener, "/api/screener")
myShare.add_resource(LivePrice, "/api/price")
myShare.add_resource(ChartData, "/api/chart")
myShare.add_resource(MarketNews, "/api/news")
myShare.add_resource(FuturesData, "/api/futures")
myShare.add_resource(ForexData, "/api/forex")

if __name__ == '__main__':
    init_screener_cache()
    app.run(port=1817)
