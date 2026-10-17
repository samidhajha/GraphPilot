from app import queries as Q

print(Q.resolve("po-1042"))
print(Q.resolve("BR-01"))
try:
    Q.resolve("hello")
except ValueError as e:
    print("OK, rejected:", e)