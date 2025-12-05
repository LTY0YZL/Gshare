## Name
Gshare

## Description
The purpose of G-Share is to transform the grocery shopping experience by creating a collaborative shopping platform that enhances the shopping experience. G-Share connects neighbors who are already planning grocery store visits with those who need groceries but lack the time, energy, or budget for traditional shopping trips or delivery options. This platform addresses a common household challenge by offering an efficient and affordable alternative to conventional delivery apps, which often charge high fees. Through G-Share, community members help each other obtain essential items, creating both convenience and neighborhood connections.

## Tech Stack
Backend: Django, Channels/WebSockets
Frontend: HTML, TailwindCSS, JavaScript
Database: MySQL, SQLite
Hosting: AWS Elastic Beanstalk, AWS S3
APIs: Kroger API, Google Maps API
AI: Gemini

## Installation
This project is a website, so the user doesn't need to install anything.

## Usage
1. Create an Account

2. Set Up Your Profile
Inside My Profile, you can:
     Update name, email, address
     Upload a profile picture
     Add a bio
    Change your password
Your address is automatically converted to map coordinates so you can see nearby shoppers and deliveries.

3.Browse Items and Add to Cart
Go to the Stores/Cart page. Search items from kroger grocery stores
Or use Voice order to order things for AI

4. Use Kroger Search (Smith’s, etc.)
GShare supports realtime Kroger product search using ZIP codes.

Go to the Store Filter and select Kroger
      Enter a ZIP code (e.g., 84102)
      Enter a product search term like “eggs”

GShare will find Kroger-owned stores near that ZIP
     Display real Kroger products
     Click Add to Cart to save Kroger items to your cart

5. Checkout Order
Once your cart is ready:
     Click Publish order

Your order becomes visible to nearby drivers on the live map.

6. Track Your Orders on the Map

The map shows:
    Your orders
    Nearby shoppers
    Stores (your order store location)

Clicking a marker opens a slide-in panel with:
    Order details
     Item list
    Buttons for taking or delivering orders

7. Become a Driver (Optional)

You can take your own order or others order
     Click an order on the map
     Press Take Order
     Turn on Start Sharing to share your live GPS location

The app will
    Track your path(Show route from you to store to customer)

When someone or you are taking your orders
       back to cart page
       click Order Requests
       Accept this person tkae your order
 
Now your can start to delivery this order

8. Group Chating
Join chat in Group page to talk to driver or join a daliy chat with others
    join a group with code other send or create a new group
    type or send image in chat

## Support
For support, send an email to this address.
email: g.sharelimited@gmail.com

## Roadmap
It is currently released to the public. We are planning to make a few small changes for the coming months, but based on traffic, we will decide to shut it down or continue it.

## Contributing
We are open to contributions, but they should be tested on the local server, then pushed to the contribution branch, which will then be decided if it is good enough to push to production.


Getting started


Step (1):
Download python


Step (2):
Create a Virtual Environment. You can use "python -m venv venv\"


Step (3):
Activate the Virtual Environment. You can use "venv\scripts\activate"


Step (4):
Install all the requirements inside the Virtual Environment. Use this in the terminal after cd to gshare_project "python -r pip install requirements.txt"


Step (5):
Make changes.


Steps (6):
Run the project on the local server for testing. Using "python manage.py runserver"

## Authors and acknowledgment
Creator
    Abdul Mansoor,
    Yang Hong,
    Jason Davies,
    Anand Palukuri

## License
MIT License

## Project status
Active Development


G-Share is currently under active development.
New features, performance improvements, and bug fixes are being added regularly.
Contributions are welcome
