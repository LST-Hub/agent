import mysql.connector
from mysql.connector import Error
from urllib.parse import urlparse

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
    DATABASE_URL = "mysql://nscruxco_ts_super_user:Tasksprint123@mi3-ss56.a2hosting.com:3306/nscruxco_tasksprint_mysql"

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
        # Do something with the connection
        # e.g., cursor = connection.cursor()
        
        # Close the connection when done
        connection.close()
        print("MySQL connection is closed")
