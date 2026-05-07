nums = [1, 2, 3, 4, 5]
if nums == sorted(nums) and len(set(nums)) == len(nums):
    print("Increasing trend")
else:
    print("Not increasing")