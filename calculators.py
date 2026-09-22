# calculators.py
#
# All the actual maths for the project lives here.
# There is no AI in this file at all, just normal Python functions.
#
# We kept these separate on purpose. The same functions are used in two
# places: the calculator cards on the website call them, and the chatbot
# calls them too when someone asks for a number. So the maths is written
# once and both parts of the app give the same answer.


def calculate_emi(principal, annual_rate_percent, tenure_months):
    # EMI is the fixed amount you pay every month to repay a loan.
    # The standard formula needs a monthly rate, but people give the
    # interest rate per year, so we divide by 12 first.
    monthly_rate = (annual_rate_percent / 100) / 12

    # If someone enters 0% interest the normal formula breaks
    # (we would divide by zero), so we just split the loan evenly.
    if monthly_rate == 0:
        return round(principal / tenure_months, 2)

    # The EMI formula, split over two lines so it is easier to read.
    emi = principal * monthly_rate * (1 + monthly_rate) ** tenure_months
    emi = emi / ((1 + monthly_rate) ** tenure_months - 1)

    return round(emi, 2)


def calculate_savings_plan(goal_amount, months):
    # The simplest function here. If you want to save 50000 in 10 months,
    # you need to put aside 5000 every month.
    monthly_amount = goal_amount / months
    return round(monthly_amount, 2)


def calculate_rd(monthly_deposit, annual_rate_percent, months):
    # In a Recurring Deposit you put in the same amount every month.
    # The important bit is that each deposit earns interest only for the
    # months it actually stays in the account. So the first deposit earns
    # interest for the full period, and the last one earns almost none.
    #
    # That is why we loop through each month instead of using one formula.
    #
    # Note: real banks usually compound RD interest every 3 months. We
    # compound monthly here to keep the code simple, so our number can be
    # slightly different from a bank's, but the idea is the same.
    monthly_rate = (annual_rate_percent / 100) / 12
    maturity = 0

    for month_number in range(1, months + 1):
        months_remaining = months - month_number + 1
        maturity += monthly_deposit * (1 + monthly_rate) ** months_remaining

    return round(maturity, 2)


def calculate_fd(principal, annual_rate_percent, years, compounds_per_year=4):
    # A Fixed Deposit is one lump sum left in the bank for a fixed time.
    # This is just the normal compound interest formula.
    #
    # compounds_per_year is 4 by default because Indian banks normally
    # add FD interest every 3 months (4 times a year).
    rate = annual_rate_percent / 100
    n = compounds_per_year

    maturity = principal * (1 + rate / n) ** (n * years)
    return round(maturity, 2)


def calculate_budget_split(income):
    # The 50/30/20 rule: half your income for needs, 30% for wants,
    # 20% for savings. We return all three so the website can show them
    # together.
    needs = round(income * 0.50, 2)
    wants = round(income * 0.30, 2)
    savings = round(income * 0.20, 2)

    return {"needs": needs, "wants": wants, "savings": savings}


def calculate_sip(monthly_investment, annual_return_percent, years):
    # A SIP is a fixed monthly investment into a mutual fund. This works
    # out what it could grow to, assuming a steady return.
    #
    # Returns are not guaranteed in real life, so this is only an estimate.
    monthly_rate = (annual_return_percent / 100) / 12
    months = years * 12

    # Same zero-rate problem as EMI. With no growth you just get back
    # whatever you put in.
    if monthly_rate == 0:
        return round(monthly_investment * months, 2)

    # Standard SIP future value formula. The extra (1 + monthly_rate) at
    # the end is because SIP payments are made at the start of each month,
    # so every payment gets one extra month of growth.
    future_value = (
        monthly_investment
        * (((1 + monthly_rate) ** months - 1) / monthly_rate)
        * (1 + monthly_rate)
    )

    return round(future_value, 2)


# This block only runs if you run this file directly with
# "python calculators.py". It does not run when the website imports it.
# We used this to check each formula gave sensible numbers before
# connecting anything to the AI.
if __name__ == "__main__":
    print("EMI test:", calculate_emi(60000, 12, 12))
    print("Savings test:", calculate_savings_plan(50000, 10))
    print("RD test:", calculate_rd(1000, 6.5, 12))
    print("FD test:", calculate_fd(10000, 7, 1))
    print("Budget split test:", calculate_budget_split(10000))
    print("SIP test:", calculate_sip(2000, 12, 5))
