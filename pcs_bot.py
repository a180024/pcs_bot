import multiprocessing
import time
import random
import json
from web3 import Web3
import asyncio
from eth_account.account import Account
import os
import time
from yaspin import yaspin
import datetime
from colorama import init
from colorama import Fore, Back, Style
import yaml
import os

config = yaml.safe_load(open("settings.yml"))
init()

with open("abis/factory.json") as f:
    uniswap_factory_abi = json.load(f)
with open("abis/router.json") as f:
    uniswap_router_abi = json.load(f)
with open("abis/wbnb.json") as f:
    uniswap_wbnb_abi = json.load(f)


# Addresses
router_address = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
factory_address = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
wbnb_address = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
scan_address = config["snipe_settings"]["snipe_address"]

web3 = Web3(Web3.HTTPProvider("https://bsc-dataseed.binance.org/"))

# Account setup
account = Account.from_key(config["account_settings"]["private_key"])
account_private_key = account.privateKey.hex()
account_address = account.address
budget = Web3.toWei(config["snipe_settings"]["budget_in_wbnb"], "ether")
gas_price = web3.toWei(config["snipe_settings"]["gasprice_in_gwei"], "gwei")
worker_count = 100

factory = web3.eth.contract(address=factory_address, abi=uniswap_factory_abi)
router = web3.eth.contract(address=router_address, abi=uniswap_router_abi)
wbnb = web3.eth.contract(address=wbnb_address, abi=uniswap_wbnb_abi)


def get_transaction_details(transaction):
    try:
        transaction_details = web3.eth.getTransaction(transaction)
        return transaction_details
    except Exception as e:
        if "TransactionNotFound" not in repr(e):
            print(Fore.RED + "Not mined yet.", repr(e))
        return None


def get_desired_token(token0, token1):
    if token0.lower() == scan_address.lower():
        desired_token = token0
    elif token1.lower() == scan_address.lower():
        desired_token = token1
    else:
        return None

    return desired_token


def buy(token):
    print(Fore.YELLOW + f"\n    *-- Buying token: {token}")
    nonce = web3.eth.get_transaction_count(account.address)

    estimated_amounts = router.functions.getAmountsOut(
        budget, [wbnb_address, token]
    ).call({"from": account.address})

    amount_out_min = estimated_amounts[1] - int(estimated_amounts[1] / 10)

    valid_until = int(datetime.datetime.now().timestamp() * 1000) + 1000 * 60 * 10

    transaction = router.functions.swapExactTokensForTokens(
        budget, amount_out_min, [wbnb_address, token], account.address, valid_until
    ).buildTransaction({"gasPrice": gas_price, "from": account_address, "nonce": nonce})
    signed_txn = web3.eth.account.signTransaction(
        transaction, private_key=account_private_key
    )
    txn_hash = web3.eth.sendRawTransaction(signed_txn.rawTransaction).hex()

    if txn_hash:
        print(
            Fore.GREEN
            + f"/n    *--- Bought successfully, Transaction: https://bscscan.com/tx/{txn_hash}"
        )
        os._exit(0)

    else:
        print(Fore.RED + f" *--- Buy failed")


def worker(input_queue):
    while True:
        transaction = input_queue.get()

        transaction_details = get_transaction_details(transaction)
        if not transaction_details:
            continue

        # Transaction is to the pancakeswap router
        if transaction_details["to"] == router_address:
            transaction_inputs = router.decode_function_input(
                transaction_details["input"]
            )
            transaction_func = transaction_inputs[0]

            desired_token = None
            if transaction_func.fn_name == "addLiquidity":
                desired_token = get_desired_token(
                    transaction_inputs[1]["tokenA"], transaction_inputs[1]["tokenB"]
                )
            elif transaction_func.fn_name == "addLiquidityETH":
                if transaction_inputs[1]["token"].lower() == scan_address.lower():
                    desired_token = transaction_inputs[1]["token"]

            if desired_token:
                print(
                    Fore.YELLOW
                    + f"\n    *-- Found liquidation for {desired_token} in transaction: https://bscscan.com/tx/{transaction.hex()}"
                )
                buy(desired_token)


def master():
    input_queue = multiprocessing.Queue()
    workers = []

    counter = 0
    with yaspin(text="Starting..", color="cyan", timer=True) as sp:
        # Create workers.
        for _ in range(worker_count):
            p = multiprocessing.Process(target=worker, args=(input_queue,))
            workers.append(p)
            p.start()

        while True:
            try:
                # Get transactions
                tx_filter = web3.eth.filter("pending")
                transaction_hashes = tx_filter.get_new_entries()
            except ValueError as e:
                if "filter not found" in str(repr(e)):
                    tx_filter = web3.eth.filter("pending")
                else:
                    print(Fore.RED + e)

            # Distribute work.
            for transaction in transaction_hashes:
                input_queue.put(transaction)

                # Might increase if rate likmited
                #  time.sleep(0.05)

            counter += len(transaction_hashes)
            sp.text = f"{counter} transactions scanned. Elapsed time:"


if __name__ == "__main__":
    master()
