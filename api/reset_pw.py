import bcrypt, sqlite3, sys

new_pw = sys.argv[1] if len(sys.argv) > 1 else input("Enter new password: ").strip()
hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
conn = sqlite3.connect('gallery.db')
conn.execute('UPDATE users SET password_hash=? WHERE username=?', (hash, 'mark'))
conn.commit()
print('Password updated for mark')
