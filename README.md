# 💊 Pharmacy Management System

A **Production-ready Pharmacy Management System** designed to streamline the management of medicines, sales, departments, and roles.  
This system supports **Admin** and **Pharmacist** user roles with custom dashboards, medicine tracking, and a secure authentication system.

---

## 🚀 Features

- **Role-based Authentication**
  - Admin and Pharmacist with separate permissions.
  - Secure login & registration.
- **Medicine Management**
  - Add, update, delete medicines.
  - Categorize medicines by department and type.
  - Stock and expiry tracking.
- **Sales Management**
  - Record and manage medicine sales.
  - Auto-generate invoices/receipts.
  - Track daily, weekly, and monthly sales.
- **Department Management**
  - Manage different medicine departments and their types.
- **Reporting & Analytics**
  - View stock levels and sales analytics.
- **Production Ready**
  - Built with scalability and security in mind.
  - Ready for real-world deployment.

---

## 🏗 Tech Stack

**Backend:** Django Rest Framework (DRF)  
**Frontend ** React
**Database:** PostgreSQL (Production) / SQLite (Development)  
**Authentication:** JWT authentication
**Others:** Celery (background tasks)

---

## 📂 Project Structure
```
pharmacy-management-system/
│
├── Backend/ # Django backend project
│ ├── pharmacy/ # Main project folder
│ ├── medicines/ # Medicines app
│ ├── sales/ # Sales app
│ ├── departments/ # Departments app
│ ├── users/ # Authentication & roles app
│ └── requirements.txt
│
├── Frontend/ # Optional frontend (React/HTML)
│
├── docs/ # Documentation & API specs
│
└── README.md
```
```bash

---

## ⚙️ Installation & Setup

### 1️⃣ Prerequisites
Make sure you have the following installed:
- [Python 3.11+](https://www.python.org/)
- [PostgreSQL](https://www.postgresql.org/)
- [Git](https://git-scm.com/)
- [Node.js](https://nodejs.org/) *(only if using React frontend)*
```
### 2️⃣ Clone the Repository
```bash
git clone https://github.com/abeni-hub/Pharmacy-MS.git
cd Pharmacy-MS
```

### **3️⃣ Create Virtual Environment**

```bash
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
.venv\Scripts\activate      # Windows
```
### ** 4 Install Dependencies**
```bash
pip install -r requirements.txt
```
### **6 Setup database**
```bash
python manage.py makemigrations
python manage.py migrate
```
### **7 Create Super User**
```bash
python manage.py createsuperuser
```
### **8 Run Development Server **
```bash
python manage.py runserver
```
### Test Medicine
```
http://127.0.0.1:8000/api/pharmacy/medicines/
```
<img width="1366" height="768" alt="image" src="https://github.com/user-attachments/assets/cf3ac43b-83fd-4b9c-9f6f-b0cfaa8aa3c5" />

### Test Sales
```bash
http://127.0.0.1:8000/api/pharmacy/sales/
```
<img width="1366" height="768" alt="image" src="https://github.com/user-attachments/assets/8c8a92c0-6aa5-41f5-a915-6a417111e670" />
