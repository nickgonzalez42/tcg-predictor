namespace API.Entities;

public class Basket
{
    public int Id {get; set;}
    public required string BasketId {get; set;}
    public List<BasketItem> Items {get; set;} = [];

    public void AddItem(Product product, int quantity)
    {
        if (product == null) ArgumentNullException.ThrowIfNull(product);
        if (quantity < 1) throw new ArgumentException("Quantity should be greater than zero", nameof(quantity));

        var existingItem = FindItem(product.Id);

        if (existingItem == null)
        {
            Items.Add(new BasketItem
            {
                Product = product,
                Quantity = quantity
            });
        } else
        {
            existingItem.Quantity += quantity;
        }
    }

    public void RemoveItem(int productId, int quantity)
    {
        if (quantity <= 0) throw new ArgumentException("Quantity should be greater than zero", nameof(quantity));

        var existingItem = FindItem(productId);
        if (existingItem == null)
        {
            return;
        } else
        {
            existingItem.Quantity -= quantity;
            if (existingItem.Quantity <= 0)
            {
                Items.Remove(existingItem);
            }
        }
    }

    private BasketItem? FindItem(int productId)
    {
        return Items.FirstOrDefault(item => item.ProductId == productId);
    }
}
