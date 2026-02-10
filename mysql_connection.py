import mysql.connector
from mysql.connector import Error
from urllib.parse import urlparse
from dotenv import load_dotenv
import os

def create_connection(host_name, user_name, user_password, db_name=None):
    """
    Establishes a connection to the MySQL database.
    
    Args:
        host_name (str): The host name or IP address of the MySQL server.
        user_name (str): The username to attempt login with.
        user_password (str): The password to attempt login with.
        db_name (str, optional): The name of the database to connect to. Defaults to None.
    
    Returns:
        mysql.connector.connection.MySQLConnection: The connection object if successful, None otherwise.
    """
    connection = None
    try:
        connection = mysql.connector.connect(
            host=host_name,
            user=user_name,
            passwd=user_password,
            database=db_name
        )
        print("MySQL Database connection successful")
    except Error as e:
        print(f"The error '{e}' occurred")

    return connection

if __name__ == "__main__":
    load_dotenv()
    DATABASE_URL = os.getenv("DATABASE_URL")

    # Parse the database URL
    url = urlparse(DATABASE_URL)
    
    # Connect to the server
    connection = create_connection(
        host_name=url.hostname,
        user_name=url.username,
        user_password=url.password,
        db_name=url.path[1:] # Remove the leading slash
    )

    if connection and connection.is_connected():
        # Get columns from tables
        cursor = connection.cursor()
        
        # Fetch user emails
        print("\nFetching User emails...")
        cursor.execute("SELECT id, name, email FROM User WHERE workspaceId = 3 AND active = 1")
        users = cursor.fetchall()
        
        email_map = {}
        for user_id, name, email in users:
            email_map[str(user_id)] = email
            
        import json
        output_file = 'data/active_emp.json'
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        
        with open(output_file, 'w') as f:
            json.dump(email_map, f, indent=4)
            
        print(f"Successfully exported {len(email_map)} emails to {output_file}")
        
        cursor.close()
        
        # Close the connection when done
        connection.close()
        print("MySQL connection is closed")
