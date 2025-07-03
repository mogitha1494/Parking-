# Parking Slot Booking System

A Python-based application for managing parking slot bookings with a Tkinter GUI and SQLite database.

## Features

- **User Interface**:
  - View available parking slots
  - Book slots with customizable duration
  - View and manage active bookings
  - Automatic slot release when booking expires

- **Admin Dashboard**:
  - View all bookings with filtering options
  - Manage parking slots (add/disable)
  - View system statistics and revenue

- **Backend**:
  - SQLite database for data persistence
  - Background thread for checking expired bookings
  - Configurable pricing system

## Technologies Used

- Python 3
- Tkinter (GUI)
- SQLite (Database)
- Threading (Background tasks)

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/mogitha1494/parking.git
   cd parking
2.Ensure you have Python 3 installed

3.Install required dependencies

## Automatic Processes
- The system automatically checks for expired bookings every 60 seconds (configurable)
- Expired bookings are automatically released and slots become available again

## License
This project is licensed under the MIT License - see the LICENSE file for details.
